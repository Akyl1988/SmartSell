from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subscriptions.plan_catalog import get_plan, normalize_plan_id
from app.models.billing import Subscription, WalletBalance, WalletTransaction

_SUB_ACTIVE = {"active", "trialing"}
_STATUS_ALIASES = {
    "trial": "trialing",
    "overdue": "past_due",
    "paused": "frozen",
}


def _normalize_status(status: str | None) -> str:
    val = (status or "").strip().lower()
    return _STATUS_ALIASES.get(val, val)


def _anchor_day_from_subscription(subscription: Subscription, *, fallback: datetime) -> int:
    anchor = getattr(subscription, "billing_anchor_day", None)
    if anchor:
        return int(anchor)
    started_at = getattr(subscription, "started_at", None)
    if started_at:
        return int(started_at.day)
    period_end = getattr(subscription, "period_end", None)
    if period_end:
        return int(period_end.day)
    return int(fallback.day)


def _add_months_anchor(dt: datetime, anchor_day: int, months: int) -> datetime:
    month_index = (dt.month - 1) + months
    year = dt.year + (month_index // 12)
    month = (month_index % 12) + 1
    last_day = monthrange(year, month)[1]
    day = min(max(anchor_day, 1), last_day)
    return dt.replace(year=year, month=month, day=day)


def _ceil_to_midnight_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


def is_subscription_active(subscription: Subscription | None, now: datetime | None = None) -> bool:
    if not subscription:
        return False
    if getattr(subscription, "deleted_at", None):
        return False

    now = now or datetime.now(UTC)
    status = _normalize_status(getattr(subscription, "status", None))

    if getattr(subscription, "canceled_at", None):
        return False

    frozen_at = getattr(subscription, "frozen_at", None)
    resumed_at = getattr(subscription, "resumed_at", None)
    if frozen_at and (resumed_at is None or resumed_at < frozen_at):
        return False

    period_end = getattr(subscription, "period_end", None)
    grace_until = getattr(subscription, "grace_until", None)

    if status in _SUB_ACTIVE:
        return period_end is None or now <= period_end

    if status == "past_due":
        return grace_until is not None and now < grace_until

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


async def activate_plan(
    db: AsyncSession,
    *,
    company_id: int,
    plan_code: str,
    now: datetime | None = None,
) -> Subscription:
    now = now or datetime.now(UTC)
    plan = get_plan(normalize_plan_id(plan_code))
    if plan is None:
        raise ValueError("Unknown plan")

    wallet = await WalletBalance.get_for_company_async(
        db,
        company_id,
        create_if_missing=True,
        currency=plan.currency,
    )

    if (wallet.currency or "").upper() != (plan.currency or "").upper():
        raise ValueError("Wallet currency mismatch")

    amount = Decimal(plan.price)
    if amount > 0:
        await wallet.debit_safe_async(
            db,
            amount,
            description="subscription_activation",
            reference_type="subscription",
            reference_id=None,
        )

    anchor_day = now.day
    period_end = _add_months_anchor(now, anchor_day, 1)

    sub = Subscription(
        company_id=company_id,
        plan=plan.plan_id,
        status="active",
        billing_cycle="monthly",
        price=amount,
        currency=plan.currency,
        started_at=now,
        period_start=now,
        period_end=period_end,
        next_billing_date=period_end,
        billing_anchor_day=anchor_day,
        grace_until=None,
    )
    db.add(sub)
    await db.flush()
    return sub


async def renew_if_due(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    grace_days: int = 3,
) -> int:
    now = now or datetime.now(UTC)
    due_stmt = (
        select(Subscription)
        .where(Subscription.deleted_at.is_(None))
        .where(Subscription.period_end.is_not(None))
        .where(Subscription.period_end <= now)
        .where(Subscription.status.in_(["active", "past_due", "trialing"]))
    )
    rows = (await db.execute(due_stmt)).scalars().all()
    processed = 0
    for sub in rows:
        processed += 1
        anchor_day = _anchor_day_from_subscription(sub, fallback=now)
        if not sub.billing_anchor_day:
            sub.billing_anchor_day = anchor_day

        base_period_end = sub.period_end or now
        plan = get_plan(normalize_plan_id(sub.plan))
        price = Decimal(plan.price) if plan else Decimal(sub.price or 0)
        currency = plan.currency if plan else (sub.currency or "KZT")

        wallet = (
            await db.execute(select(WalletBalance).where(WalletBalance.company_id == sub.company_id).limit(1))
        ).scalar_one_or_none()

        if wallet is None or (wallet.currency or "").upper() != (currency or "").upper():
            sub.status = "past_due"
            sub.grace_until = _ceil_to_midnight_utc(base_period_end + timedelta(days=grace_days))
            continue

        try:
            if price > 0:
                before = wallet.balance or Decimal("0")
                if before < price:
                    raise ValueError("Insufficient wallet balance")
                after = before - price
                wallet.balance = after
                trx = WalletTransaction(
                    wallet_id=wallet.id,
                    transaction_type="debit",
                    amount=price,
                    balance_before=before,
                    balance_after=after,
                    description="subscription_renewal",
                    reference_type="subscription",
                    reference_id=sub.id,
                )
                db.add(trx)
                await db.flush()
        except ValueError:
            sub.status = "past_due"
            sub.grace_until = _ceil_to_midnight_utc(base_period_end + timedelta(days=grace_days))
            continue

        sub.status = "active"
        sub.grace_until = None
        sub.period_start = base_period_end
        sub.period_end = _add_months_anchor(base_period_end, anchor_day, 1)
        sub.next_billing_date = sub.period_end

    return processed


__all__ = [
    "activate_plan",
    "get_company_subscription",
    "is_subscription_active",
    "renew_if_due",
]
