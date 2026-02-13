# app/api/v1/subscriptions.py
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, computed_field, condecimal, constr, field_serializer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# --- проектные зависимости (пути проверьте по вашему проекту) ---
from app.core.db import get_async_db
from app.core.rbac import has_any_role, is_platform_admin, is_store_admin, is_store_manager
from app.core.security import (
    decode_and_validate,
    is_token_revoked,
    resolve_tenant_company_id,
)
from app.core.subscriptions.plan_catalog import (
    get_plan,
    get_plan_display_name,
    list_plans,
    normalize_plan_id,
)
from app.models.billing import BillingPayment, Subscription
from app.models.company import Company
from app.models.user import User

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
ACTIVE_STATES = {"active", "trialing", "past_due", "frozen", "trial", "overdue", "paused"}  # «текущие» подписки
FINAL_STATES = {"canceled", "expired", "ended"}


# ====== Схемы ======
class SubscriptionCreate(BaseModel):
    plan: PlanName
    billing_cycle: Cycle = "monthly"
    price: condecimal(max_digits=14, decimal_places=2) = Decimal("0.00")
    currency: CurrencyCode = "KZT"
    trial_days: int = Field(0, ge=0, le=60)


class SubscriptionUpdate(BaseModel):
    plan: PlanName | None = None
    billing_cycle: Cycle | None = None
    price: condecimal(max_digits=14, decimal_places=2) | None = None
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


# ====== Утилиты доступа/времени ======
def utc_now() -> datetime:
    return datetime.now(UTC)


def next_billing_from(now: datetime, cycle: str) -> datetime:
    return now + (timedelta(days=365) if cycle == "yearly" else timedelta(days=31))


def _ceil_to_midnight_utc(dt: datetime) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


async def ensure_company(db: AsyncSession, company_id: int) -> Company:
    c = await db.get(Company, company_id)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    return c


def ensure_company_access(user, company: Company) -> None:
    """
    Правила доступа:
    - роль платформенного супер-админа: полный доступ (если у вас есть такая роль)
    - владелец/админ компании: доступ
    - иначе: 403
    Адаптируйте под ваш user/role/RBAC.
    """
    try:
        user_company_id = getattr(user, "company_id", None)
    except Exception:
        user_company_id = None

    if is_platform_admin(user):
        return
    if user_company_id == company.id and (
        is_store_admin(user) or is_store_manager(user) or has_any_role(user, {"owner", "company_admin"})
    ):
        return
    raise HTTPException(status_code=404, detail="Company not found")


async def _get_subscription_scoped(
    db: AsyncSession,
    user: User,
    subscription_id: int,
    *,
    allow_deleted: bool = False,
) -> Subscription:
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")

    stmt = select(Subscription).where(
        Subscription.id == subscription_id,
        Subscription.company_id == resolved_company_id,
    )
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.deleted_at and not allow_deleted:
        raise HTTPException(status_code=404, detail="Subscription archived")
    await ensure_sub_access(user, sub, db, allow_deleted=allow_deleted)
    return sub


async def forbid_multiple_active(db: AsyncSession, company_id: int, exclude_id: int | None = None) -> None:
    """
    Запретить более одной «текущей» подписки (active|trial|overdue|paused).

    SQLAlchemy не умеет безопасно приводить expression к bool, поэтому строим
    список условий и добавляем фильтр по id только если exclude_id передан.
    """
    clauses = [
        Subscription.company_id == company_id,
        Subscription.status.in_(list(ACTIVE_STATES)),
        Subscription.deleted_at.is_(None),
    ]
    if exclude_id is not None:
        clauses.append(Subscription.id != exclude_id)

    count = (await db.scalar(select(func.count(Subscription.id)).where(*clauses))) or 0
    if count:
        raise HTTPException(
            status_code=409,
            detail="Active subscription already exists for this company",
        )


async def ensure_sub_access(user, sub: Subscription, db: AsyncSession, *, allow_deleted: bool = False) -> Company:
    company = await db.get(Company, sub.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if sub.deleted_at and not allow_deleted:
        raise HTTPException(status_code=404, detail="Subscription archived")
    ensure_company_access(user, company)
    return company


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


@router.get("/plans", response_model=list[PlanCatalogOut])
async def list_plan_catalog(
    user: User = Depends(_auth_user),
):
    _ = user
    return list_plans()


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

    stmt = select(Subscription).where(Subscription.company_id == resolved_company_id)
    if not include_deleted:
        stmt = stmt.where(Subscription.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(Subscription.status == status_filter)
    if plan:
        normalized_plan = normalize_plan_id(plan, default=plan)
        stmt = stmt.where(Subscription.plan == normalized_plan)
    if from_date:
        stmt = stmt.where(Subscription.next_billing_date >= from_date)
    if to_date:
        stmt = stmt.where(Subscription.next_billing_date <= to_date)

    rows = (
        (
            await db.execute(
                stmt.order_by(Subscription.next_billing_date.is_(None), Subscription.next_billing_date.asc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/current", response_model=SubscriptionOut | None)
async def get_current_subscription(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    _company = await ensure_company(db, resolved_company_id)
    ensure_company_access(user, _company)

    rows = (
        (
            await db.execute(
                select(Subscription)
                .where(Subscription.company_id == resolved_company_id)
                .where(Subscription.deleted_at.is_(None))
                .where(Subscription.status.in_(list(ACTIVE_STATES)))
                .order_by(Subscription.next_billing_date.is_(None), Subscription.next_billing_date.asc())
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


@router.get("/{subscription_id}", response_model=SubscriptionOut)
async def get_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    sub = await _get_subscription_scoped(db, user, subscription_id)
    return sub


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
        # бизнес-правило: только одна «текущая» подписка
        await forbid_multiple_active(db, resolved_company_id)

        now = utc_now()
        normalized_plan = normalize_plan_id(payload.plan, default=payload.plan)
        plan = get_plan(normalized_plan, default=None)

        if payload.trial_days > 0 and not is_platform_admin(user):
            raise HTTPException(
                status_code=422,
                detail="trial_days is not allowed here; trial is granted via Kaspi merchant_uid",
            )
        if payload.trial_days < 0 or payload.trial_days > 15:
            raise HTTPException(status_code=400, detail="trial_days_invalid")

        if payload.trial_days:
            period_end = now + timedelta(days=15)
            grace_until = _ceil_to_midnight_utc(period_end + timedelta(days=3))
            status_value = "trial"
        else:
            period_end = next_billing_from(now, payload.billing_cycle)
            grace_until = None
            status_value = "active"

        sub = Subscription(
            company_id=resolved_company_id,
            plan=normalized_plan,
            status=status_value,
            billing_cycle=payload.billing_cycle,
            price=Decimal(plan.price) if plan else Decimal(payload.price),
            currency=plan.currency if plan else payload.currency,
            started_at=now,
            period_start=now,
            period_end=period_end,
            expires_at=period_end if payload.trial_days else None,
            next_billing_date=period_end,
            billing_anchor_day=now.day,
            grace_until=grace_until,
        )
        db.add(sub)
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
    sub = await _get_subscription_scoped(db, user, subscription_id)

    try:
        if payload.plan is not None:
            sub.plan = normalize_plan_id(payload.plan, default=payload.plan)
        if payload.billing_cycle is not None:
            sub.billing_cycle = payload.billing_cycle
            sub.next_billing_date = next_billing_from(utc_now(), sub.billing_cycle)
        if payload.price is not None:
            sub.price = Decimal(payload.price)
        if payload.currency is not None:
            sub.currency = payload.currency

        await db.commit()
        await db.refresh(sub)
        return sub

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
    sub = await _get_subscription_scoped(db, user, subscription_id)

    if sub.status == "canceled":
        return sub  # идемпотентно

    sub.status = "canceled"
    sub.canceled_at = utc_now()
    await db.commit()
    await db.refresh(sub)
    return sub


@router.post("/{subscription_id}/resume", response_model=SubscriptionOut)
async def resume_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    sub = await _get_subscription_scoped(db, user, subscription_id)

    # Бизнес-правило: нельзя «возобновлять», если подписка давно отменена и срок истёк (пример)
    if sub.status == "canceled" and sub.expires_at and sub.expires_at < utc_now():
        raise HTTPException(status_code=422, detail="Canceled and expired; create a new subscription")

    now = utc_now()
    sub.status = "active"
    sub.next_billing_date = next_billing_from(now, sub.billing_cycle or "monthly")
    if sub.started_at is None:
        sub.started_at = now

    # Доп. валидация: нет ли другой «текущей» подписки у компании
    await forbid_multiple_active(db, sub.company_id, exclude_id=sub.id)

    await db.commit()
    await db.refresh(sub)
    return sub


@router.post("/{subscription_id}/renew", response_model=SubscriptionOut)
async def renew_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    """
    Простое продление (вызвать после успешной оплаты).
    """
    sub = await _get_subscription_scoped(db, user, subscription_id)

    now = utc_now()
    sub.status = "active"
    sub.next_billing_date = next_billing_from(now, sub.billing_cycle or "monthly")
    await db.commit()
    await db.refresh(sub)
    return sub


@router.post("/{subscription_id}/end-trial", response_model=SubscriptionOut)
async def end_trial(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    """
    Принудительно завершить trial и перевести в active (например, после ранней оплаты).
    """
    sub = await _get_subscription_scoped(db, user, subscription_id)

    if sub.status != "trial":
        raise HTTPException(status_code=422, detail="Subscription is not in trial")

    now = utc_now()
    sub.status = "active"
    sub.expires_at = None
    sub.next_billing_date = next_billing_from(now, sub.billing_cycle or "monthly")
    await db.commit()
    await db.refresh(sub)
    return sub


@router.post("/{subscription_id}/archive", response_model=SubscriptionOut)
async def archive_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")

    sub = await _get_subscription_scoped(db, user, subscription_id, allow_deleted=True)

    if sub.deleted_at:
        return sub

    # Soft-delete uses naive UTC timestamp to match existing column type
    sub.deleted_at = utc_now().replace(tzinfo=None)
    await db.commit()
    await db.refresh(sub)
    return sub


@router.post("/{subscription_id}/restore", response_model=SubscriptionOut)
async def restore_subscription(
    subscription_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")

    sub = await _get_subscription_scoped(db, user, subscription_id, allow_deleted=True)

    sub.deleted_at = None
    await db.commit()
    await db.refresh(sub)
    return sub


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
    sub = await _get_subscription_scoped(db, user, subscription_id)
    company = await ensure_sub_access(user, sub, db)

    stmt = (
        select(BillingPayment)
        .where(
            BillingPayment.company_id == company.id,
            BillingPayment.subscription_id == subscription_id,
        )
        .order_by(BillingPayment.created_at.desc())
        .limit(100)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return rows
