from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription_catalog import Feature, Plan, PlanFeature


def _normalize_code(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_feature_code(value: str | None) -> str:
    return (value or "").strip().lower()


async def get_plan_by_code(db: AsyncSession, code: str | None) -> Plan | None:
    normalized = _normalize_code(code)
    if not normalized:
        return None
    return (await db.execute(select(Plan).where(Plan.code == normalized))).scalar_one_or_none()


async def get_feature_by_code(db: AsyncSession, code: str | None) -> Feature | None:
    normalized = _normalize_feature_code(code)
    if not normalized:
        return None
    return (await db.execute(select(Feature).where(Feature.code == normalized))).scalar_one_or_none()


async def get_plan_feature_by_codes(
    db: AsyncSession,
    *,
    plan_code: str | None,
    feature_code: str,
) -> tuple[Plan | None, Feature | None, PlanFeature | None]:
    plan_code_norm = _normalize_code(plan_code)
    feature_code_norm = _normalize_feature_code(feature_code)
    if not plan_code_norm or not feature_code_norm:
        return None, None, None
    stmt = (
        select(Plan, Feature, PlanFeature)
        .join(PlanFeature, PlanFeature.plan_id == Plan.id)
        .join(Feature, PlanFeature.feature_id == Feature.id)
        .where(Plan.code == plan_code_norm)
        .where(Feature.code == feature_code_norm)
    )
    row = (await db.execute(stmt)).first()
    if not row:
        plan = await get_plan_by_code(db, plan_code_norm)
        feature = await get_feature_by_code(db, feature_code_norm)
        return plan, feature, None
    plan, feature, plan_feature = row
    return plan, feature, plan_feature


__all__ = ["get_plan_by_code", "get_feature_by_code", "get_plan_feature_by_codes"]
