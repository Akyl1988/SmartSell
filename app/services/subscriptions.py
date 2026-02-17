from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subscriptions.catalog import get_plan_by_code
from app.core.subscriptions.plan_catalog import get_plan as get_plan_legacy
from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.core.subscriptions.state import get_company_subscription, is_subscription_active
from app.models.billing import Subscription, WalletBalance, WalletTransaction


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


async def activate_plan(
    db: AsyncSession,
    *,
    company_id: int,
    plan_code: str,
    now: datetime | None = None,
) -> Subscription:
    now = now or datetime.now(UTC)
    normalized = normalize_plan_id(plan_code, default=plan_code) or plan_code
    plan = await get_plan_by_code(db, normalized)
    plan_code = normalized
    plan_price = None
    plan_currency = None
    if plan is None:
        legacy = get_plan_legacy(normalize_plan_id(plan_code, default=None), default=None)
        if legacy is None:
            raise ValueError("Unknown plan")
        plan_code = legacy.plan_id
        plan_price = legacy.price
        plan_currency = legacy.currency
    else:
        plan_code = plan.code
        plan_price = plan.price
        plan_currency = plan.currency

    wallet = await WalletBalance.get_for_company_async(
        db,
        company_id,
        create_if_missing=True,
        currency=plan_currency or "KZT",
    )

    if (wallet.currency or "").upper() != (plan_currency or "").upper():
        raise ValueError("Wallet currency mismatch")

    amount = Decimal(str(plan_price or 0))
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
        plan=plan_code,
        status="active",
        billing_cycle="monthly",
        price=amount,
        currency=plan_currency or "KZT",
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
        plan = await get_plan_by_code(db, normalize_plan_id(sub.plan, default=sub.plan) or sub.plan)
        price = Decimal(str(plan.price)) if plan else Decimal(sub.price or 0)
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
