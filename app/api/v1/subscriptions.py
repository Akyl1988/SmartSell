# app/api/v1/subscriptions.py
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, computed_field, condecimal, constr, field_serializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# --- проектные зависимости (пути проверьте по вашему проекту) ---
from app.core.db import get_async_db
from app.core.rbac import is_platform_admin
from app.core.security import (
    decode_and_validate,
    is_token_revoked,
    resolve_tenant_company_id,
)
from app.core.subscriptions.plan_catalog import (
    get_plan_display_name,
    list_plans,
    normalize_plan_id,
)
from app.models.subscription_catalog import Plan
from app.models.user import User
from app.services.subscription_api_helpers import (
    apply_archive_subscription,
    apply_cancel_subscription,
    apply_end_trial,
    apply_renew_subscription,
    apply_restore_subscription,
    apply_resume_subscription,
    apply_subscription_update,
    build_subscription_for_create,
    ensure_company,
    ensure_company_access,
    ensure_sub_access,
    get_current_subscription_row,
    get_subscription_scoped,
    list_subscription_payments_rows,
    list_subscriptions_rows,
)
from app.services.subscription_api_helpers import (
    forbid_multiple_active as _helper_forbid_multiple_active,
)
from app.services.subscription_api_helpers import (
    next_billing_from as _helper_next_billing_from,
)
from app.services.subscription_api_helpers import (
    utc_now as _helper_utc_now,
)

# ----------------------------------------------------------------

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])
http_bearer = HTTPBearer(auto_error=False)

# ====== Константы/типы ======
PlanName = constr(min_length=2, max_length=32)
CurrencyCode = constr(min_length=3, max_length=8)
AllowedStatus = Literal[
    "active",
    "trialing",
    "past_due",
    "frozen",
    "canceled",
    "overdue",
    "trial",
    "paused",
    "expired",
    "ended",
]
Cycle = Literal["monthly", "yearly"]
FINAL_STATES = {"canceled", "expired", "ended"}


# ====== Схемы ======
class SubscriptionCreate(BaseModel):
    plan: PlanName
    billing_cycle: Cycle = "monthly"
    price: condecimal(max_digits=14, decimal_places=0) = Decimal("0")
    currency: CurrencyCode = "KZT"
    trial_days: int = Field(0, ge=0, le=60)


class SubscriptionUpdate(BaseModel):
    plan: PlanName | None = None
    billing_cycle: Cycle | None = None
    price: condecimal(max_digits=14, decimal_places=0) | None = None
    currency: CurrencyCode | None = None


class SubscriptionOut(BaseModel):
    id: int
    company_id: int
    plan: str
    status: str
    billing_cycle: str
    price: Decimal
    currency: str
    started_at: datetime | None
    expires_at: datetime | None
    next_billing_date: datetime | None
    canceled_at: datetime | None = None
    ended_at: datetime | None = None
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("plan")
    def _serialize_plan(self, plan: str) -> str:
        return get_plan_display_name(plan)

    @computed_field(return_type=str)
    @property
    def plan_id(self) -> str:
        return normalize_plan_id(self.plan, default=self.plan) or (self.plan or "")


class PaymentOut(BaseModel):
    id: int
    provider: str | None = None
    status: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PlanCatalogOut(BaseModel):
    plan_id: str
    plan: str
    currency: str
    monthly_price: Decimal
    yearly_price: Decimal


async def _auth_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    db: AsyncSession = Depends(get_async_db),
) -> User:
    """Resolve User from Bearer token without threadpool to keep async-safe under Trio."""

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_and_validate(credentials.credentials, expected_type="access")
    except Exception as e:  # decode errors → 401
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    try:
        user_id = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def utc_now() -> datetime:
    return _helper_utc_now()


def next_billing_from(now: datetime, cycle: str) -> datetime:
    return _helper_next_billing_from(now, cycle)


async def forbid_multiple_active(db: AsyncSession, company_id: int, exclude_id: int | None = None) -> None:
    await _helper_forbid_multiple_active(db, company_id, exclude_id=exclude_id)


def _ceil_to_midnight_utc(dt: datetime) -> datetime:
    from app.services.subscription_api_helpers import ceil_to_midnight_utc

    return ceil_to_midnight_utc(dt)


async def _get_subscription_scoped(
    db: AsyncSession,
    user: User,
    subscription_id: int,
    *,
    allow_deleted: bool = False,
):
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    return await get_subscription_scoped(
        db,
        user,
        subscription_id,
        resolved_company_id,
        allow_deleted=allow_deleted,
    )


async def _load_scoped_subscription(
    db: AsyncSession,
    user: User,
    subscription_id: int,
    *,
    allow_deleted: bool = False,
):
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    return await get_subscription_scoped(db, user, subscription_id, resolved_company_id, allow_deleted=allow_deleted)


async def _commit_refresh_return(db: AsyncSession, subscription):
    await db.commit()
    await db.refresh(subscription)
    return subscription


@router.get("/plans", response_model=list[PlanCatalogOut])
async def list_plan_catalog(
    user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
):
    _ = user
    rows = (await db.execute(select(Plan).order_by(Plan.id.asc()))).scalars().all()
    if not rows:
        return [PlanCatalogOut(**item) for item in list_plans()]
    items: list[PlanCatalogOut] = []
    for plan in rows:
        monthly_price = Decimal(str(plan.price or 0))
        yearly_price = monthly_price * Decimal("12")
        items.append(
            PlanCatalogOut(
                plan_id=plan.code,
                plan=plan.name,
                currency=plan.currency,
                monthly_price=monthly_price,
                yearly_price=yearly_price,
            )
        )
    return items


# ====== Эндпоинты ======
@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    status_filter: AllowedStatus | None = Query(None),
    plan: str | None = Query(None, max_length=32),
    from_date: datetime | None = Query(None, description="Фильтр по next_billing_date (>=)"),
    to_date: datetime | None = Query(None, description="Фильтр по next_billing_date (<=)"),
    include_deleted: bool = Query(False, description="Только для админов: включать архивные"),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    _company = await ensure_company(db, resolved_company_id)
    ensure_company_access(user, _company)

    include_deleted = include_deleted if is_platform_admin(user) else False
    return await list_subscriptions_rows(
        db,
        company_id=resolved_company_id,
        include_deleted=include_deleted,
        status_filter=status_filter,
        plan=plan,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/current", response_model=SubscriptionOut | None)
async def get_current_subscription(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    _company = await ensure_company(db, resolved_company_id)
    ensure_company_access(user, _company)
    return await get_current_subscription_row(db, company_id=resolved_company_id)


@router.get("/{subscription_id}", response_model=SubscriptionOut)
async def get_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    return await _load_scoped_subscription(db, user, subscription_id)


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    payload: SubscriptionCreate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    _company = await ensure_company(db, resolved_company_id)
    ensure_company_access(user, _company)

    try:
        sub = await build_subscription_for_create(
            db,
            company_id=resolved_company_id,
            payload=payload,
            user=user,
        )
        await db.commit()
        await db.refresh(sub)
        return sub

    except IntegrityError as e:
        await db.rollback()
        logger.exception("IntegrityError on create_subscription: %s", e)
        raise HTTPException(status_code=409, detail="Duplicate or constraint violation")
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("Unexpected error on create_subscription: %s", e)
        raise HTTPException(status_code=500, detail="Subscription creation failed")


@router.patch("/{subscription_id}", response_model=SubscriptionOut)
async def update_subscription(
    subscription_id: int = Path(..., ge=1),
    payload: SubscriptionUpdate = ...,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    sub = await _load_scoped_subscription(db, user, subscription_id)

    try:
        apply_subscription_update(sub, payload)
        return await _commit_refresh_return(db, sub)

    except IntegrityError as e:
        await db.rollback()
        logger.exception("IntegrityError on update_subscription: %s", e)
        raise HTTPException(status_code=409, detail="Duplicate or constraint violation")
    except Exception as e:
        await db.rollback()
        logger.exception("Unexpected error on update_subscription: %s", e)
        raise HTTPException(status_code=500, detail="Subscription update failed")


@router.post("/{subscription_id}/cancel", response_model=SubscriptionOut)
async def cancel_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    sub = await _load_scoped_subscription(db, user, subscription_id)

    if sub.status == "canceled":
        return sub  # идемпотентно

    apply_cancel_subscription(sub)
    return await _commit_refresh_return(db, sub)


@router.post("/{subscription_id}/resume", response_model=SubscriptionOut)
async def resume_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    sub = await _load_scoped_subscription(db, user, subscription_id)

    await apply_resume_subscription(db, sub)
    return await _commit_refresh_return(db, sub)


@router.post("/{subscription_id}/renew", response_model=SubscriptionOut)
async def renew_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    """
    Простое продление (вызвать после успешной оплаты).
    """
    sub = await _load_scoped_subscription(db, user, subscription_id)

    apply_renew_subscription(sub)
    return await _commit_refresh_return(db, sub)


@router.post("/{subscription_id}/end-trial", response_model=SubscriptionOut)
async def end_trial(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    """
    Принудительно завершить trial и перевести в active (например, после ранней оплаты).
    """
    sub = await _load_scoped_subscription(db, user, subscription_id)

    apply_end_trial(sub)
    return await _commit_refresh_return(db, sub)


@router.post("/{subscription_id}/archive", response_model=SubscriptionOut)
async def archive_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")

    sub = await _load_scoped_subscription(db, user, subscription_id, allow_deleted=True)

    if sub.deleted_at:
        return sub

    # Soft-delete uses naive UTC timestamp to match existing column type
    apply_archive_subscription(sub)
    return await _commit_refresh_return(db, sub)


@router.post("/{subscription_id}/restore", response_model=SubscriptionOut)
async def restore_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")

    sub = await _load_scoped_subscription(db, user, subscription_id, allow_deleted=True)

    apply_restore_subscription(sub)
    return await _commit_refresh_return(db, sub)


@router.get("/{subscription_id}/payments", response_model=list[PaymentOut])
async def list_subscription_payments(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    """
    История платежей, связанных с подпиской (упрощённо: по company_id и plan).
    В проде лучше иметь Subscription->Payment связь явно.
    """
    sub = await _load_scoped_subscription(db, user, subscription_id)
    company = await ensure_sub_access(user, sub, db)
    return await list_subscription_payments_rows(db, company_id=company.id, subscription_id=subscription_id)
