from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_verified_user,
    get_pagination,
    require_company_access,
    require_store_roles,
)
from app.core.entitlements import require_entitlement
from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.core.features import FEATURE_REPRICING
from app.core.quotas import QUOTA_REPRICING_RULES, check_quota
from app.core.security import resolve_tenant_company_id
from app.models.product import Product
from app.models.repricing import RepricingDiff, RepricingRule, RepricingRun, repricing_run_stats
from app.models.user import User
from app.schemas.base import SuccessResponse
from app.schemas.pricing import (
    PricingApplyRequest,
    PricingApplyResponse,
    PricingPreviewItem,
    PricingPreviewRequest,
    PricingRuleCreate,
    PricingRuleListResponse,
    PricingRuleResponse,
    PricingRuleUpdate,
)
from app.services.pricing_engine import RuleConfig, evaluate_product
from app.services.subscription_features import enforce_feature_limit

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


read_router = APIRouter(
    prefix="/pricing",
    tags=["pricing"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(require_store_roles("admin", "manager", "employee")),
        Depends(_require_company_context),
        Depends(require_entitlement(FEATURE_REPRICING)),
    ],
)
admin_router = APIRouter(
    prefix="/pricing",
    tags=["pricing"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_roles("admin", "manager")),
        Depends(require_entitlement(FEATURE_REPRICING)),
    ],
)


def _rule_config_from_model(rule: RepricingRule) -> RuleConfig:
    return RuleConfig(
        min_price=rule.min_price,
        max_price=rule.max_price,
        step=rule.step,
        undercut=rule.undercut,
        cooldown_seconds=rule.cooldown_seconds,
        max_delta_percent=rule.max_delta_percent,
    )


def _rule_config_from_payload(payload: Any) -> RuleConfig:
    return RuleConfig(
        min_price=payload.min_price,
        max_price=payload.max_price,
        step=payload.step,
        undercut=payload.undercut,
        cooldown_seconds=payload.cooldown_seconds,
        max_delta_percent=payload.max_delta_percent,
    )


async def _get_rule_or_404(db: AsyncSession, rule_id: int, company_id: int) -> RepricingRule:
    result = await db.execute(
        select(RepricingRule).where(RepricingRule.id == rule_id, RepricingRule.company_id == company_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("Rule not found", "RULE_NOT_FOUND")
    return rule


def _validate_rule_bounds(min_price: Decimal | None, max_price: Decimal | None) -> None:
    if min_price is not None and max_price is not None and min_price > max_price:
        raise SmartSellValidationError("min_price cannot be greater than max_price", "INVALID_PRICE_BOUNDS")


def _apply_product_filters(stmt, payload) -> Any:
    if not payload:
        return stmt
    if payload.product_ids:
        stmt = stmt.where(Product.id.in_(payload.product_ids))
    if payload.category_id is not None:
        stmt = stmt.where(Product.category_id == payload.category_id)
    if payload.sku:
        stmt = stmt.where(Product.sku == payload.sku)
    if payload.name_contains:
        stmt = stmt.where(Product.name.ilike(f"%{payload.name_contains}%"))
    if payload.min_price is not None:
        stmt = stmt.where(Product.price >= payload.min_price)
    if payload.max_price is not None:
        stmt = stmt.where(Product.price <= payload.max_price)
    if payload.is_active is not None:
        stmt = stmt.where(Product.is_active.is_(payload.is_active))
    return stmt


def _apply_scope_filters(stmt, scope: dict[str, Any] | None) -> Any:
    if not scope:
        return stmt
    scope_type = str(scope.get("type") or "all").strip().lower()
    if scope_type == "all":
        return stmt
    if scope_type == "product_ids":
        raw_ids = scope.get("product_ids") or scope.get("ids") or []
        ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
        if not ids:
            return stmt.where(Product.id == 0)
        return stmt.where(Product.id.in_(ids))
    if scope_type == "sku_list":
        raw_skus = scope.get("sku_list") or scope.get("skus") or []
        skus = [str(s).strip().upper() for s in raw_skus if str(s).strip()]
        if not skus:
            return stmt.where(Product.id == 0)
        return stmt.where(Product.sku.in_(skus))
    raise SmartSellValidationError("Unsupported scope type", "INVALID_SCOPE")


@read_router.get("/rules", response_model=PricingRuleListResponse)
async def list_rules(
    include_inactive: bool = Query(False),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    stmt = select(RepricingRule).where(RepricingRule.company_id == company_id)
    if not include_inactive:
        stmt = stmt.where(RepricingRule.is_active.is_(True))

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(RepricingRule.id.desc()).offset(pagination.offset).limit(pagination.limit)
    items = (await db.execute(items_stmt)).scalars().all()

    return PricingRuleListResponse.create(items=items, total=total, page=pagination.page, per_page=pagination.per_page)


@admin_router.post("/rules", response_model=PricingRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: PricingRuleCreate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    await check_quota(db, company_id=company_id, quota_key=QUOTA_REPRICING_RULES)
    _validate_rule_bounds(payload.min_price, payload.max_price)
    rule = RepricingRule(company_id=company_id, **payload.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@read_router.get("/rules/{rule_id}", response_model=PricingRuleResponse)
async def get_rule(
    rule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return await _get_rule_or_404(db, rule_id, company_id)


@admin_router.patch("/rules/{rule_id}", response_model=PricingRuleResponse)
async def update_rule(
    payload: PricingRuleUpdate,
    rule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    rule = await _get_rule_or_404(db, rule_id, company_id)
    data = payload.model_dump(exclude_unset=True)

    min_price = data.get("min_price", rule.min_price)
    max_price = data.get("max_price", rule.max_price)
    _validate_rule_bounds(min_price, max_price)

    for key, value in data.items():
        setattr(rule, key, value)

    await db.commit()
    await db.refresh(rule)
    return rule


@admin_router.delete("/rules/{rule_id}", response_model=SuccessResponse)
async def delete_rule(
    rule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    rule = await _get_rule_or_404(db, rule_id, company_id)
    if not rule.is_active:
        return SuccessResponse(message="Rule already inactive")
    rule.is_active = False
    rule.enabled = False
    await db.commit()
    return SuccessResponse(message="Rule deleted successfully")


@admin_router.post("/preview", response_model=list[PricingPreviewItem])
async def preview_pricing(
    payload: PricingPreviewRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    rule: RepricingRule | None = None
    if payload.rule_id:
        rule = await _get_rule_or_404(db, payload.rule_id, company_id)
        if not rule.is_active or not rule.enabled:
            raise SmartSellValidationError("Rule is disabled", "RULE_DISABLED")
        rule_cfg = _rule_config_from_model(rule)
        scope = rule.scope or {}
    else:
        rule_cfg = _rule_config_from_payload(payload.rule)
        scope = {}

    stmt = select(Product).where(Product.company_id == company_id, Product.deleted_at.is_(None))
    stmt = _apply_product_filters(stmt, payload.filters)
    stmt = _apply_scope_filters(stmt, scope)
    if payload.filters and payload.filters.limit:
        stmt = stmt.limit(payload.filters.limit)
    products = (await db.execute(stmt)).scalars().all()

    now = datetime.utcnow()
    items: list[PricingPreviewItem] = []
    for product in products:
        decision = evaluate_product(product, rule_cfg, now=now)
        items.append(
            PricingPreviewItem(
                product_id=decision.product_id,
                old_price=decision.old_price,
                new_price=decision.new_price,
                reason=decision.reason,
            )
        )

    return items


@admin_router.post("/apply", response_model=PricingApplyResponse)
async def apply_pricing(
    payload: PricingApplyRequest,
    request: Request,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    nested = db.in_transaction()
    tx = db.begin_nested() if nested else db.begin()
    async with tx:
        rule = await _get_rule_or_404(db, payload.rule_id, company_id)
        if not rule.is_active or not rule.enabled:
            raise SmartSellValidationError("Rule is disabled", "RULE_DISABLED")

        now = datetime.utcnow()
        run = RepricingRun(
            company_id=company_id,
            rule_id=rule.id,
            status="running",
            started_at=now,
            requested_by_user_id=getattr(current_user, "id", None),
            request_id=request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID"),
        )
        db.add(run)
        await db.flush()

        rule_cfg = _rule_config_from_model(rule)
        stmt = select(Product).where(Product.company_id == company_id, Product.deleted_at.is_(None))
        stmt = _apply_product_filters(stmt, payload.filters)
        stmt = _apply_scope_filters(stmt, rule.scope or {})
        stmt = stmt.order_by(Product.id.asc()).with_for_update()
        if payload.filters and payload.filters.limit:
            stmt = stmt.limit(payload.filters.limit)
        products = (await db.execute(stmt)).scalars().all()

        decisions: list[tuple[Product, PricingPreviewItem, dict[str, Any] | None]] = []
        changed_count = 0
        for product in products:
            try:
                decision = evaluate_product(product, rule_cfg, now=now)
                item = PricingPreviewItem(
                    product_id=decision.product_id,
                    old_price=decision.old_price,
                    new_price=decision.new_price,
                    reason=decision.reason,
                )
                decisions.append((product, item, decision.meta))
                if decision.new_price is not None:
                    changed_count += 1
            except Exception as exc:
                item = PricingPreviewItem(
                    product_id=int(getattr(product, "id", 0) or 0),
                    old_price=getattr(product, "price", None),
                    new_price=None,
                    reason="error",
                )
                decisions.append((product, item, {"error": str(exc)}))

        await enforce_feature_limit(
            db,
            company_id=company_id,
            feature_code=FEATURE_REPRICING,
            increment_by=changed_count,
            limit_key="max_products_per_period",
            now=now,
        )

        processed = 0
        changed = 0
        skipped = 0
        errors = 0
        diffs: list[PricingPreviewItem] = []

        for product, item, meta in decisions:
            processed += 1
            if item.reason == "error":
                errors += 1
                diffs.append(item)
                db.add(
                    RepricingDiff(
                        company_id=company_id,
                        rule_id=rule.id,
                        run_id=run.id,
                        product_id=product.id,
                        sku=product.sku,
                        old_price=getattr(product, "price", None),
                        new_price=None,
                        reason="error",
                        meta=meta or {},
                    )
                )
                continue

            if item.new_price is None:
                skipped += 1
                diffs.append(item)
                continue

            product.set_price_guarded(item.new_price, update_timestamps=True, respect_bounds=True)
            product.repriced_at = now
            changed += 1
            diffs.append(item)
            db.add(
                RepricingDiff(
                    company_id=company_id,
                    rule_id=rule.id,
                    run_id=run.id,
                    product_id=product.id,
                    sku=product.sku,
                    old_price=item.old_price,
                    new_price=item.new_price,
                    reason=item.reason,
                    meta=meta or {},
                )
            )

        run.stats = repricing_run_stats(processed=processed, changed=changed, skipped=skipped, errors=errors)
        run.status = "completed_with_errors" if errors else "completed"
        run.finished_at = datetime.utcnow()

    if nested:
        await db.commit()
    await db.refresh(run)
    return PricingApplyResponse(run_id=run.id, stats=run.stats or {}, diffs=diffs)


router.include_router(read_router)
router.include_router(admin_router)
