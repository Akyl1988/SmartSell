from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import has_any_role, is_platform_admin, is_store_admin, is_store_manager
from app.core.subscriptions.plan_catalog import get_plan, normalize_plan_id
from app.models.billing import BillingPayment, Subscription
from app.models.company import Company
from app.models.user import User

ACTIVE_STATES = {"active", "trialing", "past_due", "frozen", "trial", "overdue", "paused"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def next_billing_from(now: datetime, cycle: str) -> datetime:
    return now + (timedelta(days=365) if cycle == "yearly" else timedelta(days=31))


def ceil_to_midnight_utc(dt: datetime) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


async def ensure_company(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def ensure_company_access(user: User, company: Company) -> None:
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


async def ensure_sub_access(
    user: User,
    subscription: Subscription,
    db: AsyncSession,
    *,
    allow_deleted: bool = False,
) -> Company:
    company = await db.get(Company, subscription.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if subscription.deleted_at and not allow_deleted:
        raise HTTPException(status_code=404, detail="Subscription archived")
    ensure_company_access(user, company)
    return company


async def get_subscription_scoped(
    db: AsyncSession,
    user: User,
    subscription_id: int,
    resolved_company_id: int,
    *,
    allow_deleted: bool = False,
) -> Subscription:
    stmt = select(Subscription).where(
        Subscription.id == subscription_id,
        Subscription.company_id == resolved_company_id,
    )
    subscription = (await db.execute(stmt)).scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if subscription.deleted_at and not allow_deleted:
        raise HTTPException(status_code=404, detail="Subscription archived")
    await ensure_sub_access(user, subscription, db, allow_deleted=allow_deleted)
    return subscription


async def forbid_multiple_active(db: AsyncSession, company_id: int, exclude_id: int | None = None) -> None:
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


async def list_subscriptions_rows(
    db: AsyncSession,
    *,
    company_id: int,
    include_deleted: bool,
    status_filter: str | None,
    plan: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
) -> list[Subscription]:
    stmt = select(Subscription).where(Subscription.company_id == company_id)
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


async def get_current_subscription_row(db: AsyncSession, *, company_id: int) -> Subscription | None:
    rows = (
        (
            await db.execute(
                select(Subscription)
                .where(Subscription.company_id == company_id)
                .where(Subscription.deleted_at.is_(None))
                .where(Subscription.status.in_(list(ACTIVE_STATES)))
                .order_by(Subscription.next_billing_date.is_(None), Subscription.next_billing_date.asc())
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


async def build_subscription_for_create(
    db: AsyncSession,
    *,
    company_id: int,
    payload: Any,
    user: User,
) -> Subscription:
    await forbid_multiple_active(db, company_id)

    now = utc_now()
    normalized_plan = normalize_plan_id(payload.plan, default=payload.plan)
    plan = get_plan(normalized_plan, default=None)

    is_pro = normalized_plan == "pro"
    requested_trial_days = int(payload.trial_days or 0)

    if requested_trial_days > 0 and not is_platform_admin(user) and not is_pro:
        raise HTTPException(
            status_code=422,
            detail="trial_days is not allowed here; trial is granted via Kaspi merchant_uid",
        )
    if requested_trial_days < 0 or requested_trial_days > 15:
        raise HTTPException(status_code=400, detail="trial_days_invalid")

    effective_trial_days = requested_trial_days
    if is_pro:
        effective_trial_days = 15

    if effective_trial_days:
        period_end = now + timedelta(days=15)
        grace_until = ceil_to_midnight_utc(period_end + timedelta(days=3))
        status_value = "trial"
    else:
        period_end = next_billing_from(now, payload.billing_cycle)
        grace_until = None
        status_value = "active"

    subscription = Subscription(
        company_id=company_id,
        plan=normalized_plan,
        status=status_value,
        billing_cycle=payload.billing_cycle,
        price=Decimal(plan.price) if plan else Decimal(payload.price),
        currency=plan.currency if plan else payload.currency,
        started_at=now,
        period_start=now,
        period_end=period_end,
        expires_at=period_end if effective_trial_days else None,
        next_billing_date=period_end,
        billing_anchor_day=now.day,
        grace_until=grace_until,
        trial_used=bool(effective_trial_days),
    )
    db.add(subscription)
    return subscription


def apply_subscription_update(subscription: Subscription, payload: Any) -> None:
    if payload.plan is not None:
        subscription.plan = normalize_plan_id(payload.plan, default=payload.plan)
    if payload.billing_cycle is not None:
        subscription.billing_cycle = payload.billing_cycle
        subscription.next_billing_date = next_billing_from(utc_now(), subscription.billing_cycle)
    if payload.price is not None:
        subscription.price = Decimal(payload.price)
    if payload.currency is not None:
        subscription.currency = payload.currency


def apply_cancel_subscription(subscription: Subscription) -> None:
    subscription.status = "canceled"
    subscription.canceled_at = utc_now()


async def apply_resume_subscription(db: AsyncSession, subscription: Subscription) -> None:
    if subscription.status == "canceled" and subscription.expires_at and subscription.expires_at < utc_now():
        raise HTTPException(status_code=422, detail="Canceled and expired; create a new subscription")

    now = utc_now()
    subscription.status = "active"
    subscription.next_billing_date = next_billing_from(now, subscription.billing_cycle or "monthly")
    if subscription.started_at is None:
        subscription.started_at = now

    await forbid_multiple_active(db, subscription.company_id, exclude_id=subscription.id)


def apply_renew_subscription(subscription: Subscription) -> None:
    now = utc_now()
    subscription.status = "active"
    subscription.next_billing_date = next_billing_from(now, subscription.billing_cycle or "monthly")


def apply_end_trial(subscription: Subscription) -> None:
    if subscription.status != "trial":
        raise HTTPException(status_code=422, detail="Subscription is not in trial")

    now = utc_now()
    subscription.status = "active"
    subscription.expires_at = None
    subscription.next_billing_date = next_billing_from(now, subscription.billing_cycle or "monthly")


def apply_archive_subscription(subscription: Subscription) -> None:
    subscription.deleted_at = utc_now().replace(tzinfo=None)


def apply_restore_subscription(subscription: Subscription) -> None:
    subscription.deleted_at = None


async def list_subscription_payments_rows(
    db: AsyncSession,
    *,
    company_id: int,
    subscription_id: int,
) -> list[BillingPayment]:
    stmt = (
        select(BillingPayment)
        .where(
            BillingPayment.company_id == company_id,
            BillingPayment.subscription_id == subscription_id,
        )
        .order_by(BillingPayment.created_at.desc())
        .limit(100)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return rows
