"""Preorder policy evaluation for out-of-stock products."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.core.subscriptions.state import get_company_subscription, is_subscription_active
from app.models.company import Company
from app.models.product import Product
from app.services.kaspi_stock_truth import compute_kaspi_stock_truth

_POLICY_ENABLED_KEY = "preorders.auto_on_oos"
_POLICY_MIN_LEAD_DAYS_KEY = "preorders.auto_on_oos_min_lead_days"
_POLICY_TOP_ONLY_KEY = "preorders.auto_on_oos_top_plan_only"

_TOP_PLANS = {"pro"}


def _load_company_settings(company: Company | None) -> dict[str, Any]:
    if not company or not company.settings:
        return {}
    try:
        return json.loads(company.settings) or {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _policy_enabled(settings: dict[str, Any]) -> bool:
    return bool(settings.get(_POLICY_ENABLED_KEY, False))


def _policy_min_lead_days(settings: dict[str, Any]) -> int:
    raw = settings.get(_POLICY_MIN_LEAD_DAYS_KEY, 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _policy_top_only(settings: dict[str, Any]) -> bool:
    return bool(settings.get(_POLICY_TOP_ONLY_KEY, True))


async def _plan_allows_auto(db: AsyncSession, company_id: int, *, top_only: bool) -> bool:
    if not top_only:
        return True
    subscription = await get_company_subscription(db, company_id)
    if not is_subscription_active(subscription):
        return False
    plan_code = normalize_plan_id(getattr(subscription, "plan", None)) or getattr(subscription, "plan", None)
    return (plan_code or "") in _TOP_PLANS


def _get_preorder_mode(product: Product) -> str | None:
    data = product.get_extra()
    preorder_cfg = data.get("preorder")
    if not isinstance(preorder_cfg, dict):
        return None
    mode = preorder_cfg.get("mode")
    if mode in {"auto", "manual"}:
        return mode
    return None


def _set_preorder_mode(product: Product, mode: str | None) -> None:
    data = product.get_extra()
    preorder_cfg = data.get("preorder")
    if not isinstance(preorder_cfg, dict):
        preorder_cfg = {}
    if mode:
        preorder_cfg["mode"] = mode
    else:
        preorder_cfg.pop("mode", None)
    data["preorder"] = preorder_cfg
    product.set_extra(data)


async def evaluate_preorder_state(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
) -> dict[str, Any]:
    company = await db.get(Company, company_id)
    settings = _load_company_settings(company)
    if not _policy_enabled(settings):
        return {"changed": False, "effective_stock": None}

    if not await _plan_allows_auto(db, company_id, top_only=_policy_top_only(settings)):
        return {"changed": False, "effective_stock": None}

    product = (
        (await db.execute(select(Product).where(Product.id == product_id, Product.company_id == company_id)))
        .scalars()
        .first()
    )
    if not product:
        return {"changed": False, "effective_stock": None}

    truth = await compute_kaspi_stock_truth(db, company_id=company_id, product_id=product_id)
    effective_stock = truth.local_effective_stock
    if effective_stock is None:
        return {"changed": False, "effective_stock": None}
    min_lead_days = _policy_min_lead_days(settings)
    mode = _get_preorder_mode(product)
    changed = False

    if effective_stock <= 0:
        target_lead_days = max(int(product.preorder_lead_days or 0), min_lead_days)
        if mode == "manual":
            if not product.is_preorder_enabled:
                product.enable_preorder(lead_days=target_lead_days or None)
                changed = True
            elif min_lead_days and (product.preorder_lead_days or 0) < min_lead_days:
                product.enable_preorder(lead_days=target_lead_days or None)
                changed = True
        else:
            if not product.is_preorder_enabled:
                product.enable_preorder(lead_days=target_lead_days or None)
                _set_preorder_mode(product, "auto")
                changed = True
            else:
                if mode != "auto":
                    _set_preorder_mode(product, "auto")
                    changed = True
                if min_lead_days and (product.preorder_lead_days or 0) < min_lead_days:
                    product.enable_preorder(lead_days=target_lead_days or None)
                    changed = True
    else:
        if mode == "auto" and product.is_preorder_enabled:
            product.disable_preorder()
            _set_preorder_mode(product, None)
            changed = True

    if changed:
        await db.flush()

    return {"changed": changed, "effective_stock": effective_stock, "mode": mode}
