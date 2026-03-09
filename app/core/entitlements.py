from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.features import is_feature_enabled_for_plan
from app.core.rbac import is_platform_admin
from app.core.security import get_current_user, resolve_tenant_company_id
from app.core.subscriptions.catalog import get_plan_feature_by_codes
from app.core.subscriptions.errors import build_subscription_required_payload_for_company
from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.core.subscriptions.state import get_company_subscription, is_subscription_active


def _feature_not_available_payload(*, feature: str, plan: str | None) -> dict[str, Any]:
    return {
        "code": "FEATURE_NOT_AVAILABLE",
        "message": "Feature is not available for the current plan",
        "feature": feature,
        "plan": plan,
    }


def require_entitlement(feature: str) -> Any:
    normalized_feature = (feature or "").strip().lower()

    async def _dep(
        request: Request,
        current_user=Depends(get_current_user),  # noqa: B008
        db: AsyncSession = Depends(get_async_db),  # noqa: B008
    ) -> Any:
        if is_platform_admin(current_user):
            return current_user

        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        subscription = await get_company_subscription(db, company_id)
        if not is_subscription_active(subscription):
            payload = await build_subscription_required_payload_for_company(db, company_id)
            raise HTTPException(status_code=402, detail=payload)

        plan_code = normalize_plan_id(getattr(subscription, "plan", None)) or getattr(subscription, "plan", None)
        plan, feat, plan_feature = await get_plan_feature_by_codes(
            db,
            plan_code=plan_code,
            feature_code=normalized_feature,
        )
        if plan and feat and plan_feature is not None:
            enabled = bool(plan.is_active and feat.is_active and plan_feature.enabled)
            if not enabled:
                raise HTTPException(
                    status_code=403,
                    detail=_feature_not_available_payload(
                        feature=normalized_feature, plan=getattr(plan, "code", plan_code)
                    ),
                )
            return current_user

        if not is_feature_enabled_for_plan(plan_code, normalized_feature):
            raise HTTPException(
                status_code=403,
                detail=_feature_not_available_payload(feature=normalized_feature, plan=plan_code),
            )

        return current_user

    return _dep


__all__ = ["require_entitlement"]
