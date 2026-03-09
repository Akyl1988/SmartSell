from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SmartSellValidationError
from app.core.subscriptions.plan_catalog import get_plan_quotas, normalize_plan_id
from app.core.subscriptions.state import get_company_subscription
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.preorder import Preorder
from app.models.product import Product
from app.models.repricing import RepricingRule
from app.models.warehouse import Warehouse

QUOTA_PRODUCTS = "products"
QUOTA_CAMPAIGNS = "campaigns"
QUOTA_REPRICING_RULES = "repricing_rules"
QUOTA_WAREHOUSES = "warehouses"
QUOTA_PREORDERS = "preorders"
QUOTA_API_HEAVY_OPERATIONS = "api_heavy_operations"


@dataclass(frozen=True)
class QuotaLimit:
    key: str
    plan_id: str
    limit: int | None


class QuotaExceededError(SmartSellValidationError):
    def __init__(self, *, quota_key: str, plan_id: str, limit: int, current: int):
        super().__init__(
            f"Quota exceeded for '{quota_key}': {current}/{limit}",
            code="QUOTA_EXCEEDED",
            http_status=409,
            extra={
                "quota_key": quota_key,
                "plan_id": plan_id,
                "limit": limit,
                "current": current,
            },
        )


async def _count_products(db: AsyncSession, company_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Product.id)).where(Product.company_id == company_id, Product.deleted_at.is_(None))
            )
        ).scalar_one()
    )


async def _count_campaigns(db: AsyncSession, company_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Campaign.id)).where(Campaign.company_id == company_id, Campaign.deleted_at.is_(None))
            )
        ).scalar_one()
    )


async def _count_repricing_rules(db: AsyncSession, company_id: int) -> int:
    return int(
        (
            await db.execute(select(func.count(RepricingRule.id)).where(RepricingRule.company_id == company_id))
        ).scalar_one()
    )


async def _count_warehouses(db: AsyncSession, company_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Warehouse.id)).where(
                    Warehouse.company_id == company_id, Warehouse.deleted_at.is_(None)
                )
            )
        ).scalar_one()
    )


async def _count_preorders(db: AsyncSession, company_id: int) -> int:
    return int(
        (await db.execute(select(func.count(Preorder.id)).where(Preorder.company_id == company_id))).scalar_one()
    )


_USAGE_COUNTERS = {
    QUOTA_PRODUCTS: _count_products,
    QUOTA_CAMPAIGNS: _count_campaigns,
    QUOTA_REPRICING_RULES: _count_repricing_rules,
    QUOTA_WAREHOUSES: _count_warehouses,
    QUOTA_PREORDERS: _count_preorders,
}


async def _resolve_company_plan(db: AsyncSession, company_id: int) -> str:
    subscription = await get_company_subscription(db, company_id)
    if subscription is not None:
        return normalize_plan_id(getattr(subscription, "plan", None)) or "start"

    company = await db.get(Company, company_id)
    fallback_plan = getattr(company, "subscription_plan", None) if company else None
    return normalize_plan_id(fallback_plan) or "start"


async def check_quota(
    db: AsyncSession,
    *,
    company_id: int,
    quota_key: str,
    current_usage: int | None = None,
) -> QuotaLimit:
    normalized_key = (quota_key or "").strip().lower()
    plan_id = await _resolve_company_plan(db, company_id)

    plan_quotas = get_plan_quotas(plan_id)
    if normalized_key not in plan_quotas:
        raise SmartSellValidationError(
            f"Unknown quota key: {normalized_key}",
            code="UNKNOWN_QUOTA_KEY",
            http_status=422,
        )

    limit_raw = plan_quotas.get(normalized_key)
    limit_value = int(limit_raw) if limit_raw is not None else None
    quota_limit = QuotaLimit(key=normalized_key, plan_id=plan_id, limit=limit_value)

    if limit_value is None or limit_value < 0:
        return quota_limit

    usage = current_usage
    if usage is None:
        counter = _USAGE_COUNTERS.get(normalized_key)
        if counter is None:
            usage = 0
        else:
            usage = await counter(db, company_id)

    if int(usage) >= limit_value:
        raise QuotaExceededError(
            quota_key=normalized_key,
            plan_id=plan_id,
            limit=limit_value,
            current=int(usage),
        )

    return quota_limit


__all__ = [
    "QUOTA_PRODUCTS",
    "QUOTA_CAMPAIGNS",
    "QUOTA_REPRICING_RULES",
    "QUOTA_WAREHOUSES",
    "QUOTA_PREORDERS",
    "QUOTA_API_HEAVY_OPERATIONS",
    "QuotaLimit",
    "QuotaExceededError",
    "check_quota",
]
