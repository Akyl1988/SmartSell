from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.logging import get_logger
from app.core.rbac import is_platform_admin
from app.core.security import get_current_user, resolve_tenant_company_id
from app.core.subscriptions.errors import (
    build_subscription_required_payload,
    build_subscription_required_payload_for_company,
)
from app.core.subscriptions.plan_catalog import get_plan_features as _catalog_plan_features
from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.services.subscriptions import get_company_subscription, is_subscription_active

logger = get_logger(__name__)

FEATURE_KASPI_ORDERS_LIST = "kaspi.orders_list"
FEATURE_KASPI_SYNC_NOW = "kaspi.sync_now"
FEATURE_KASPI_GOODS_IMPORTS = "kaspi.goods_imports"
FEATURE_KASPI_FEED_UPLOADS = "kaspi.feed_uploads"
FEATURE_KASPI_AUTOSYNC = "kaspi.autosync"


async def _resolve_plan(db: AsyncSession, company_id: int) -> str:
    subscription = await get_company_subscription(db, company_id)
    if subscription and is_subscription_active(subscription):
        return normalize_plan_id(getattr(subscription, "plan", None)) or "start"
    payload = await build_subscription_required_payload_for_company(db, company_id)
    raise HTTPException(status_code=402, detail=payload)


def _has_feature(plan: str, feature: str) -> bool:
    return feature in _catalog_plan_features(plan)


async def _extract_merchant_uid(request: Request) -> str | None:
    try:
        for key in ("merchantUid", "merchant_uid"):
            raw = request.query_params.get(key)
            if raw:
                return raw.strip() or None
    except Exception:
        pass

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return None

    try:
        body = await request.json()
    except Exception:
        return None
    if isinstance(body, dict):
        raw = body.get("merchant_uid") or body.get("merchantUid")
        if isinstance(raw, str):
            return raw.strip() or None
    return None


def require_feature(feature: str) -> Any:
    async def _dep(
        request: Request,
        current_user=Depends(get_current_user),  # noqa: B008
        db: AsyncSession = Depends(get_async_db),  # noqa: B008
    ) -> Any:
        if is_platform_admin(current_user):
            return current_user
        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        plan = await _resolve_plan(db, company_id)
        if not _has_feature(plan, feature):
            logger.info("Feature blocked", extra={"feature": feature, "plan": plan, "company_id": company_id})
            payload = await build_subscription_required_payload(db, current_user)
            raise HTTPException(status_code=402, detail=payload)
        return current_user

    return _dep


def get_plan_features(plan: str) -> Iterable[str]:
    return _catalog_plan_features(normalize_plan_id(plan) or "start")


__all__ = [
    "FEATURE_KASPI_ORDERS_LIST",
    "FEATURE_KASPI_SYNC_NOW",
    "FEATURE_KASPI_GOODS_IMPORTS",
    "FEATURE_KASPI_FEED_UPLOADS",
    "FEATURE_KASPI_AUTOSYNC",
    "require_feature",
    "get_plan_features",
]
