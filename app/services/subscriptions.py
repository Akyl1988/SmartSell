from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import audit_logger
from app.core.subscriptions.plan_catalog import get_plan, normalize_plan_id
from app.models.billing import Subscription, WalletBalance

_SUB_ACTIVE = {"active", "trialing"}
_SUB_INACTIVE = {"canceled", "frozen", "past_due"}
_STATUS_ALIASES = {
    "trial": "trialing",
    "overdue": "past_due",
    "paused": "frozen",
}


def _normalize_status(status: str | None) -> str:
    val = (status or "").strip().lower()
    return _STATUS_ALIASES.get(val, val)


def is_subscription_active(subscription: Subscription | None, now: datetime | None = None) -> bool:
    if not subscription:
        return False
    if getattr(subscription, "deleted_at", None):
        return False

    now = now or datetime.now(UTC)
    status = _normalize_status(getattr(subscription, "status", None))

    if status in _SUB_INACTIVE:
        return False
    if getattr(subscription, "canceled_at", None):
        return False

    frozen_at = getattr(subscription, "frozen_at", None)
    resumed_at = getattr(subscription, "resumed_at", None)
    if frozen_at and (resumed_at is None or resumed_at < frozen_at):
        return False

    period_end = getattr(subscription, "period_end", None)
    if period_end and now > period_end:
        return False

    if status in _SUB_ACTIVE:
        return True

    if status == "active":
        return True

    return False


async def get_company_subscription(db: AsyncSession, company_id: int) -> Subscription | None:
    stmt = (
        select(Subscription)
        .where(Subscription.company_id == company_id)
        .where(Subscription.deleted_at.is_(None))
        .order_by(nullslast(desc(Subscription.period_end)))
        .order_by(desc(Subscription.started_at))
        .order_by(desc(Subscription.created_at))
        .limit(1)
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


def _add_months_anniversary(dt: datetime, months: int = 1) -> datetime:
    if months == 0:
        return dt
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    last_day = monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


async def activate_plan(
    db: AsyncSession,
    *,
    company_id: int,
    plan_code: str,
    now: datetime | None = None,
) -> Subscription:
    now = now or datetime.now(UTC)
    plan_id = normalize_plan_id(plan_code, default=None)
    plan = get_plan(plan_id, default=None)
    if not plan_id or plan is None:
        raise ValueError("plan_not_found")

    wallet = await WalletBalance.get_for_company_async(db, company_id, create_if_missing=True, currency=plan.currency)
    if wallet.currency != plan.currency:
        raise ValueError("wallet_currency_mismatch")

    sub = await get_company_subscription(db, company_id)
    if sub is None:
        sub = Subscription(
            company_id=company_id,
            plan=plan_id,
            status="active",
            billing_cycle="monthly",
            price=plan.price,
            currency=plan.currency,
            started_at=now,
        )
        db.add(sub)
        await db.flush()
    else:
        sub.plan = plan_id
        sub.status = "active"
        sub.billing_cycle = "monthly"
        sub.price = plan.price
        sub.currency = plan.currency
        if not sub.started_at:
            sub.started_at = now

    if plan.price > Decimal("0"):
        if (wallet.balance or Decimal("0")) < plan.price:
            raise ValueError("insufficient_wallet_balance")
        await wallet.debit_safe_async(
            db,
            plan.price,
            description="subscription_charge",
            reference_type="subscription",
            reference_id=sub.id,
        )

    sub.period_start = now
    sub.period_end = _add_months_anniversary(now, 1)
    sub.next_billing_date = sub.period_end

    audit_logger.log_system_event(
        level="info",
        event="subscription_activated",
        message="Subscription activated",
        meta={
            "company_id": company_id,
            "plan": plan_id,
            "price": str(plan.price),
            "currency": plan.currency,
        },
    )

    await db.flush()
    return sub


async def renew_if_due(db: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    stmt = (
        select(Subscription)
        .where(Subscription.deleted_at.is_(None))
        .where(Subscription.period_end.isnot(None))
        .where(Subscription.period_end <= now)
    )
    rows = (await db.execute(stmt)).scalars().all()
    processed = 0
    for sub in rows:
        plan_id = normalize_plan_id(sub.plan, default=None)
        plan = get_plan(plan_id, default=None)
        if not plan_id or plan is None:
            sub.status = "past_due"
            processed += 1
            continue

        try:
            wallet = await WalletBalance.get_for_company_async(
                db, sub.company_id, create_if_missing=False, currency=plan.currency
            )
        except Exception:
            sub.status = "past_due"
            processed += 1
            continue
        if wallet.currency != plan.currency:
            sub.status = "past_due"
            processed += 1
            continue

        if plan.price > Decimal("0"):
            if (wallet.balance or Decimal("0")) < plan.price:
                sub.status = "past_due"
                processed += 1
                continue
            await wallet.debit_safe_async(
                db,
                plan.price,
                description="subscription_renewal",
                reference_type="subscription",
                reference_id=sub.id,
            )

        sub.status = "active"
        anchor = sub.period_end or now
        sub.period_start = anchor
        sub.period_end = _add_months_anniversary(anchor, 1)
        sub.next_billing_date = sub.period_end
        processed += 1

    return processed


__all__ = [
    "get_company_subscription",
    "is_subscription_active",
    "activate_plan",
    "renew_if_due",
]
