"""Repricing rule evaluation and run execution (store-level)."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SmartSellValidationError
from app.models.product import Product
from app.models.repricing import RepricingRule, RepricingRun, RepricingRunItem

_ALLOWED_SCOPE_TYPES = {"all", "product", "category", "brand"}
_ALLOWED_ROUNDING = {"nearest", "floor", "ceil"}


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _normalize_scope_type(value: str | None) -> str:
    if not value:
        return "all"
    return str(value).strip().lower()


def _normalize_rounding(value: str | None) -> str:
    if not value:
        return "nearest"
    return str(value).strip().lower()


def _apply_rounding(value: Decimal, step: Decimal | None, rounding_mode: str) -> Decimal:
    if step is None or step <= 0:
        return value
    multiplier = value / step
    if rounding_mode == "floor":
        return step * multiplier.to_integral_value(rounding=ROUND_FLOOR)
    if rounding_mode == "ceil":
        return step * multiplier.to_integral_value(rounding=ROUND_CEILING)
    return step * multiplier.to_integral_value(rounding=ROUND_HALF_UP)


def _clamp(value: Decimal, min_price: Decimal | None, max_price: Decimal | None) -> Decimal:
    out = value
    if min_price is not None and out < min_price:
        out = min_price
    if max_price is not None and out > max_price:
        out = max_price
    return out


def validate_rule(rule: RepricingRule) -> None:
    scope_type = _normalize_scope_type(rule.scope_type)
    if scope_type not in _ALLOWED_SCOPE_TYPES:
        raise SmartSellValidationError("Invalid scope_type", code="INVALID_SCOPE")
    if scope_type != "all" and not rule.scope_value:
        raise SmartSellValidationError("scope_value is required", code="INVALID_SCOPE")

    min_price = _as_decimal(rule.min_price)
    max_price = _as_decimal(rule.max_price)
    if min_price is not None and max_price is not None and min_price > max_price:
        raise SmartSellValidationError("min_price cannot be greater than max_price", code="INVALID_PRICE_BOUNDS")

    step = _as_decimal(rule.step)
    if step is not None and step <= 0:
        raise SmartSellValidationError("step must be positive", code="INVALID_STEP")

    rounding_mode = _normalize_rounding(rule.rounding_mode)
    if rounding_mode not in _ALLOWED_ROUNDING:
        raise SmartSellValidationError("Invalid rounding_mode", code="INVALID_ROUNDING")


def compute_new_price(old_price: Decimal | None, rule: RepricingRule) -> Decimal | None:
    if old_price is None:
        return None

    step = _as_decimal(rule.step)
    min_price = _as_decimal(rule.min_price)
    max_price = _as_decimal(rule.max_price)
    rounding_mode = _normalize_rounding(rule.rounding_mode)

    target = old_price
    if step is not None and step > 0:
        target = old_price - step

    target = _clamp(target, min_price, max_price)
    target = _apply_rounding(target, step, rounding_mode)
    target = _clamp(target, min_price, max_price)
    target = target.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if target == old_price:
        return None
    return target


def _get_brand(product: Product) -> str:
    try:
        extra = product.get_extra()
    except Exception:
        return ""
    brand = extra.get("brand")
    if brand:
        return str(brand).strip()
    repricing = extra.get("repricing")
    if isinstance(repricing, dict) and repricing.get("brand"):
        return str(repricing.get("brand")).strip()
    return ""


def _matches_brand(product: Product, scope_value: str) -> bool:
    if not scope_value:
        return False
    return _get_brand(product).lower() == scope_value.strip().lower()


def _apply_scope_filter(stmt, scope_type: str, scope_value: str | None):
    if scope_type == "product":
        if scope_value and str(scope_value).isdigit():
            return stmt.where(Product.id == int(scope_value))
        return stmt.where(Product.id == 0)
    if scope_type == "category":
        if scope_value and str(scope_value).isdigit():
            return stmt.where(Product.category_id == int(scope_value))
        return stmt.where(Product.category_id == 0)
    return stmt


def _collect_products_for_rule(
    products: list[Product],
    scope_type: str,
    scope_value: str | None,
) -> list[Product]:
    if scope_type != "brand":
        return products
    return [p for p in products if _matches_brand(p, scope_value or "")]


def _build_run_item(
    *,
    run_id: int,
    product: Product | None,
    old_price: Decimal | None,
    new_price: Decimal | None,
    reason: str,
    status: str,
    error: str | None = None,
) -> RepricingRunItem:
    return RepricingRunItem(
        run_id=run_id,
        product_id=getattr(product, "id", None),
        old_price=old_price,
        new_price=new_price,
        reason=reason,
        status=status,
        error=error,
    )


async def run_reprcing_for_company(
    db: AsyncSession,
    company_id: int,
    *,
    triggered_by_user_id: int | None = None,
    dry_run: bool = False,
    request_id: str | None = None,
) -> RepricingRun:
    now = datetime.utcnow()
    run = RepricingRun(
        company_id=company_id,
        status="running",
        started_at=now,
        triggered_by_user_id=triggered_by_user_id,
        request_id=request_id,
    )
    db.add(run)
    await db.flush()

    rules = (
        (
            await db.execute(
                select(RepricingRule)
                .where(
                    RepricingRule.company_id == company_id,
                    RepricingRule.enabled.is_(True),
                    RepricingRule.is_active.is_(True),
                )
                .order_by(RepricingRule.id.asc())
            )
        )
        .scalars()
        .all()
    )

    processed = 0
    changed = 0
    failed = 0
    last_error = None
    processed_products: set[int] = set()
    updates: list[dict[str, Any]] = []

    for rule in rules:
        validate_rule(rule)
        scope_type = _normalize_scope_type(rule.scope_type)
        scope_value = str(rule.scope_value) if rule.scope_value is not None else None

        stmt = select(Product).where(Product.company_id == company_id, Product.deleted_at.is_(None))
        stmt = _apply_scope_filter(stmt, scope_type, scope_value)
        stmt = stmt.order_by(Product.id.asc())
        products = (await db.execute(stmt)).scalars().all()
        products = _collect_products_for_rule(list(products), scope_type, scope_value)

        for product in products:
            if product.id in processed_products:
                continue
            processed_products.add(product.id)
            processed += 1

            try:
                old_price = _as_decimal(getattr(product, "price", None))
                new_price = compute_new_price(old_price, rule)

                if new_price is None:
                    item = _build_run_item(
                        run_id=run.id,
                        product=product,
                        old_price=old_price,
                        new_price=None,
                        reason="no_change",
                        status="skipped",
                    )
                    db.add(item)
                    continue

                if not dry_run:
                    product.set_price_guarded(new_price, update_timestamps=True, respect_bounds=True)
                    updates.append({"product_id": product.id, "new_price": new_price})

                reason = "dry_run" if dry_run else "repriced"
                status = "changed"
                item = _build_run_item(
                    run_id=run.id,
                    product=product,
                    old_price=old_price,
                    new_price=new_price,
                    reason=reason,
                    status=status,
                )
                db.add(item)
                changed += 1
            except Exception as exc:
                failed += 1
                last_error = str(exc)
                item = _build_run_item(
                    run_id=run.id,
                    product=product,
                    old_price=_as_decimal(getattr(product, "price", None)),
                    new_price=None,
                    reason="error",
                    status="failed",
                    error=str(exc),
                )
                db.add(item)

    if updates and not dry_run:
        try:
            from app.integrations.marketplaces.kaspi.pricing import apply_price_updates

            apply_price_updates(company_id, updates)
        except Exception as exc:
            failed += 1
            last_error = str(exc)

    run.processed = processed
    run.changed = changed
    run.failed = failed
    run.last_error = last_error
    run.finished_at = datetime.utcnow()
    run.status = "failed" if failed else "done"

    return run
