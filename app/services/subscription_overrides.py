from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription_override import SubscriptionOverride


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def is_subscription_override_active(
    db: AsyncSession,
    company_id: int,
    provider: str,
    merchant_uid: str,
    *,
    now: datetime | None = None,
) -> bool:
    if not merchant_uid:
        return False
    check_time = now or _utc_now()
    stmt = select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company_id,
        SubscriptionOverride.provider == provider,
        SubscriptionOverride.merchant_uid == merchant_uid,
        SubscriptionOverride.revoked_at.is_(None),
    )
    row = (await db.execute(stmt)).scalars().first()
    if not row:
        return False
    if row.active_until is None:
        return True
    return row.active_until > check_time
