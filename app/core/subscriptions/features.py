from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.exceptions import AuthorizationError
from app.core.logging import get_logger
from app.core.security import get_current_user, resolve_tenant_company_id
from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.company import Company
from app.services.subscriptions import get_company_subscription, is_subscription_active

logger = get_logger(__name__)

FEATURE_KASPI_ORDERS_LIST = "kaspi.orders_list"
FEATURE_KASPI_SYNC_NOW = "kaspi.sync_now"
FEATURE_KASPI_GOODS_IMPORTS = "kaspi.goods_imports"
FEATURE_KASPI_FEED_UPLOADS = "kaspi.feed_uploads"
FEATURE_KASPI_AUTOSYNC = "kaspi.autosync"

_FEATURE_MATRIX: dict[str, set[str]] = {
    "start": {
        FEATURE_KASPI_ORDERS_LIST,
    },
    "business": {
        FEATURE_KASPI_ORDERS_LIST,
        FEATURE_KASPI_SYNC_NOW,
        FEATURE_KASPI_GOODS_IMPORTS,
        FEATURE_KASPI_FEED_UPLOADS,
        FEATURE_KASPI_AUTOSYNC,
    },
    "pro": {
        FEATURE_KASPI_ORDERS_LIST,
        FEATURE_KASPI_SYNC_NOW,
        FEATURE_KASPI_GOODS_IMPORTS,
        FEATURE_KASPI_FEED_UPLOADS,
        FEATURE_KASPI_AUTOSYNC,
    },
}


async def _resolve_plan(db: AsyncSession, company_id: int) -> str:
    subscription = await get_company_subscription(db, company_id)
    if subscription and is_subscription_active(subscription):
        return normalize_plan_id(getattr(subscription, "plan", None)) or "start"

    res = await db.execute(select(Company.subscription_plan).where(Company.id == company_id))
    plan = res.scalar_one_or_none()
    return normalize_plan_id(plan) or "start"


def _has_feature(plan: str, feature: str) -> bool:
    return feature in _FEATURE_MATRIX.get(plan, set())


def require_feature(feature: str) -> Any:
    async def _dep(
        current_user=Depends(get_current_user),  # noqa: B008
        db: AsyncSession = Depends(get_async_db),  # noqa: B008
    ) -> Any:
        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        plan = await _resolve_plan(db, company_id)
        if not _has_feature(plan, feature):
            logger.info("Feature blocked", extra={"feature": feature, "plan": plan, "company_id": company_id})
            raise AuthorizationError(
                "subscription_required",
                code="subscription_required",
                http_status=402,
                extra={"feature": feature, "plan": plan, "company_id": company_id},
            )
        return current_user

    return _dep


def get_plan_features(plan: str) -> Iterable[str]:
    return _FEATURE_MATRIX.get(normalize_plan_id(plan) or "start", set())


__all__ = [
    "FEATURE_KASPI_ORDERS_LIST",
    "FEATURE_KASPI_SYNC_NOW",
    "FEATURE_KASPI_GOODS_IMPORTS",
    "FEATURE_KASPI_FEED_UPLOADS",
    "FEATURE_KASPI_AUTOSYNC",
    "require_feature",
    "get_plan_features",
]
