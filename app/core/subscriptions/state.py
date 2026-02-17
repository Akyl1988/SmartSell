from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Subscription

_SUB_ACTIVE = {"active", "trialing"}
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


__all__ = ["get_company_subscription", "is_subscription_active"]
