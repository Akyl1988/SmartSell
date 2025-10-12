# app/api/v1/subscriptions.py
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, condecimal, constr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# --- проектные зависимости (пути проверьте по вашему проекту) ---
from app.core.db import get_db
from app.core.security import get_current_user  # -> текущий пользователь
from app.models.billing import BillingPayment, Subscription
from app.models.company import Company

# ----------------------------------------------------------------

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])

# ====== Константы/типы ======
PlanName = constr(min_length=2, max_length=32)
CurrencyCode = constr(min_length=3, max_length=8)
AllowedStatus = Literal["active", "canceled", "overdue", "trial", "paused"]
Cycle = Literal["monthly", "yearly"]
ACTIVE_STATES = {"active", "trial", "overdue", "paused"}  # «текущие» подписки


# ====== Схемы ======
class SubscriptionCreate(BaseModel):
    company_id: int = Field(..., ge=1)
    plan: PlanName
    billing_cycle: Cycle = "monthly"
    price: condecimal(max_digits=14, decimal_places=2) = Decimal("0.00")
    currency: CurrencyCode = "KZT"
    trial_days: int = Field(0, ge=0, le=60)


class SubscriptionUpdate(BaseModel):
    plan: Optional[PlanName] = None
    billing_cycle: Optional[Cycle] = None
    price: Optional[condecimal(max_digits=14, decimal_places=2)] = None
    currency: Optional[CurrencyCode] = None


class SubscriptionOut(BaseModel):
    id: int
    company_id: int
    plan: str
    status: str
    billing_cycle: str
    price: Decimal
    currency: str
    started_at: Optional[datetime]
    expires_at: Optional[datetime]
    next_billing_date: Optional[datetime]

    class Config:
        from_attributes = True


class PaymentOut(BaseModel):
    id: int
    provider: Optional[str] = None
    status: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ====== Утилиты доступа/времени ======
def utc_now() -> datetime:
    return datetime.now(UTC)


def next_billing_from(now: datetime, cycle: str) -> datetime:
    return now + (timedelta(days=365) if cycle == "yearly" else timedelta(days=31))


def ensure_company(db: Session, company_id: int) -> Company:
    c = db.get(Company, company_id)
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
        role = getattr(user, "role", None)
        user_company_id = getattr(user, "company_id", None)
    except Exception:
        role, user_company_id = None, None

    if role in {"platform_admin", "superadmin"}:
        return
    if user_company_id == company.id and role in {"owner", "company_admin", "manager"}:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def forbid_multiple_active(db: Session, company_id: int) -> None:
    """
    Запретить более одной «текущей» подписки (active|trial|overdue|paused).
    """
    count = (
        db.scalar(
            select(func.count(Subscription.id)).where(
                (Subscription.company_id == company_id)
                & (Subscription.status.in_(list(ACTIVE_STATES)))
            )
        )
        or 0
    )
    if count:
        raise HTTPException(
            status_code=409,
            detail="Active subscription already exists for this company",
        )


def ensure_sub_access(user, sub: Subscription, db: Session) -> Company:
    company = db.get(Company, sub.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    ensure_company_access(user, company)
    return company


# ====== Эндпоинты ======
@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(
    company_id: int = Query(..., ge=1),
    status_filter: Optional[AllowedStatus] = Query(None),
    plan: Optional[str] = Query(None, max_length=32),
    from_date: Optional[datetime] = Query(None, description="Фильтр по next_billing_date (>=)"),
    to_date: Optional[datetime] = Query(None, description="Фильтр по next_billing_date (<=)"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    company = ensure_company(db, company_id)
    ensure_company_access(user, company)

    stmt = select(Subscription).where(Subscription.company_id == company_id)
    if status_filter:
        stmt = stmt.where(Subscription.status == status_filter)
    if plan:
        stmt = stmt.where(Subscription.plan == plan)
    if from_date:
        stmt = stmt.where(Subscription.next_billing_date >= from_date)
    if to_date:
        stmt = stmt.where(Subscription.next_billing_date <= to_date)

    rows = (
        db.execute(
            stmt.order_by(
                Subscription.next_billing_date.is_(None), Subscription.next_billing_date.asc()
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/current", response_model=SubscriptionOut | None)
def get_current_subscription(
    company_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    company = ensure_company(db, company_id)
    ensure_company_access(user, company)

    rows = (
        db.execute(
            select(Subscription)
            .where(Subscription.company_id == company_id)
            .where(Subscription.status.in_(list(ACTIVE_STATES)))
            .order_by(
                Subscription.next_billing_date.is_(None), Subscription.next_billing_date.asc()
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_subscription(
    payload: SubscriptionCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    company = ensure_company(db, payload.company_id)
    ensure_company_access(user, company)

    try:
        # бизнес-правило: только одна «текущая» подписка
        forbid_multiple_active(db, payload.company_id)

        now = utc_now()
        sub = Subscription(
            company_id=payload.company_id,
            plan=payload.plan,
            status="trial" if payload.trial_days > 0 else "active",
            billing_cycle=payload.billing_cycle,
            price=Decimal(payload.price),
            currency=payload.currency,
            started_at=now,
            expires_at=(now + timedelta(days=payload.trial_days)) if payload.trial_days else None,
            next_billing_date=next_billing_from(now, payload.billing_cycle),
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub

    except IntegrityError as e:
        db.rollback()
        logger.exception("IntegrityError on create_subscription: %s", e)
        raise HTTPException(status_code=409, detail="Duplicate or constraint violation")
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Unexpected error on create_subscription: %s", e)
        raise HTTPException(status_code=500, detail="Subscription creation failed")


@router.patch("/{subscription_id}", response_model=SubscriptionOut)
def update_subscription(
    subscription_id: int = Path(..., ge=1),
    payload: SubscriptionUpdate = ...,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    company = ensure_sub_access(user, sub, db)

    try:
        if payload.plan is not None:
            sub.plan = payload.plan
        if payload.billing_cycle is not None:
            sub.billing_cycle = payload.billing_cycle
            sub.next_billing_date = next_billing_from(utc_now(), sub.billing_cycle)
        if payload.price is not None:
            sub.price = Decimal(payload.price)
        if payload.currency is not None:
            sub.currency = payload.currency

        db.commit()
        db.refresh(sub)
        return sub

    except IntegrityError as e:
        db.rollback()
        logger.exception("IntegrityError on update_subscription: %s", e)
        raise HTTPException(status_code=409, detail="Duplicate or constraint violation")
    except Exception as e:
        db.rollback()
        logger.exception("Unexpected error on update_subscription: %s", e)
        raise HTTPException(status_code=500, detail="Subscription update failed")


@router.post("/{subscription_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    subscription_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    company = ensure_sub_access(user, sub, db)

    if sub.status == "canceled":
        return sub  # идемпотентно

    sub.status = "canceled"
    sub.canceled_at = utc_now()
    db.commit()
    db.refresh(sub)
    return sub


@router.post("/{subscription_id}/resume", response_model=SubscriptionOut)
def resume_subscription(
    subscription_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    company = ensure_sub_access(user, sub, db)

    # Бизнес-правило: нельзя «возобновлять», если подписка давно отменена и срок истёк (пример)
    if sub.status == "canceled" and sub.expires_at and sub.expires_at < utc_now():
        raise HTTPException(
            status_code=422, detail="Canceled and expired; create a new subscription"
        )

    now = utc_now()
    sub.status = "active"
    sub.next_billing_date = next_billing_from(now, sub.billing_cycle or "monthly")
    if sub.started_at is None:
        sub.started_at = now

    # Доп. валидация: нет ли другой «текущей» подписки у компании
    forbid_multiple_active(db, sub.company_id)

    db.commit()
    db.refresh(sub)
    return sub


@router.post("/{subscription_id}/renew", response_model=SubscriptionOut)
def renew_subscription(
    subscription_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Простое продление (вызвать после успешной оплаты).
    """
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    company = ensure_sub_access(user, sub, db)

    now = utc_now()
    sub.status = "active"
    sub.next_billing_date = next_billing_from(now, sub.billing_cycle or "monthly")
    db.commit()
    db.refresh(sub)
    return sub


@router.post("/{subscription_id}/end-trial", response_model=SubscriptionOut)
def end_trial(
    subscription_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Принудительно завершить trial и перевести в active (например, после ранней оплаты).
    """
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    company = ensure_sub_access(user, sub, db)

    if sub.status != "trial":
        raise HTTPException(status_code=422, detail="Subscription is not in trial")

    now = utc_now()
    sub.status = "active"
    sub.expires_at = None
    sub.next_billing_date = next_billing_from(now, sub.billing_cycle or "monthly")
    db.commit()
    db.refresh(sub)
    return sub


@router.get("/{subscription_id}/payments", response_model=list[PaymentOut])
def list_subscription_payments(
    subscription_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    История платежей, связанных с подпиской (упрощённо: по company_id и plan).
    В проде лучше иметь Subscription->Payment связь явно.
    """
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    company = ensure_sub_access(user, sub, db)

    stmt = (
        select(BillingPayment)
        .where(BillingPayment.company_id == company.id)
        .order_by(BillingPayment.created_at.desc())
        .limit(100)
    )
    rows = db.execute(stmt).scalars().all()
    return rows
