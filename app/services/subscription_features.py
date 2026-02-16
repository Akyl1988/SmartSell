from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subscriptions.catalog import get_plan_feature_by_codes
from app.core.subscriptions.errors import build_limit_exceeded_payload, build_subscription_required_payload_for_company
from app.core.subscriptions.state import get_company_subscription, is_subscription_active
from app.models.subscription_catalog import FeatureUsage


def _resolve_period_bounds(subscription, now: datetime) -> tuple[datetime, datetime]:
    period_start = getattr(subscription, "period_start", None) or getattr(subscription, "started_at", None) or now
    period_end = getattr(subscription, "period_end", None)
    if period_end is None:
        period_end = period_start + timedelta(days=30)
    return period_start, period_end


async def require_active_subscription_or_402(db: AsyncSession, company_id: int) -> Any:
    subscription = await get_company_subscription(db, company_id)
    if not is_subscription_active(subscription):
        payload = await build_subscription_required_payload_for_company(db, company_id)
        raise HTTPException(status_code=402, detail=payload)
    return subscription


async def enforce_feature_limit(
    db: AsyncSession,
    *,
    company_id: int,
    feature_code: str,
    increment_by: int,
    limit_key: str,
    now: datetime | None = None,
) -> FeatureUsage | None:
    if increment_by <= 0:
        return None
    check_time = now or datetime.now(UTC)
    subscription = await require_active_subscription_or_402(db, company_id)
    plan, feature, plan_feature = await get_plan_feature_by_codes(
        db,
        plan_code=getattr(subscription, "plan", None),
        feature_code=feature_code,
    )
    if not plan or not feature or not plan_feature or not plan_feature.enabled:
        payload = await build_subscription_required_payload_for_company(db, company_id)
        raise HTTPException(status_code=402, detail=payload)

    limits = plan_feature.limits_json or {}
    raw_limit = limits.get(limit_key)
    limit_value = None
    if raw_limit is not None:
        try:
            limit_value = int(raw_limit)
        except (TypeError, ValueError):
            limit_value = None

    period_start, period_end = _resolve_period_bounds(subscription, check_time)

    stmt = (
        select(FeatureUsage)
        .where(FeatureUsage.company_id == company_id)
        .where(FeatureUsage.feature_id == feature.id)
        .where(FeatureUsage.subscription_id == subscription.id)
        .with_for_update()
    )
    usage = (await db.execute(stmt)).scalar_one_or_none()
    if usage is None:
        usage = FeatureUsage(
            company_id=company_id,
            feature_id=feature.id,
            subscription_id=subscription.id,
            period_start=period_start,
            period_end=period_end,
            used_count=0,
        )
        db.add(usage)
        await db.flush()

    current = int(usage.used_count or 0)
    if limit_value is not None and limit_value >= 0:
        if current + increment_by > limit_value:
            payload = build_limit_exceeded_payload(
                feature=feature.code,
                limit=limit_value,
                used=current,
            )
            raise HTTPException(status_code=402, detail=payload)

    usage.used_count = current + increment_by
    await db.flush()
    return usage
