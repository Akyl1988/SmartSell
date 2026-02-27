"""Repricing rule evaluation and run execution (store-level)."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.models.company import Company
from app.models.product import Product
from app.models.repricing import RepricingRule, RepricingRun, RepricingRunItem

_ALLOWED_SCOPE_TYPES = {"all", "product", "category", "brand"}
_ALLOWED_ROUNDING = {"nearest", "floor", "ceil"}
_CONSTRAINT_REASON_MAP = {
    "ck_prod_price_nonneg": "negative_price",
    "ck_prod_price_ge_min": "below_min_price",
    "ck_prod_price_le_max": "above_max_price",
    "ck_prod_price_ge_cost_when_not_dumping": "below_cost_price",
    "ck_prod_sale_le_price": "below_sale_price",
    "ck_prod_sale_ge_min": "below_min_price",
    "ck_prod_preorder_deposit_le_price": "below_preorder_deposit",
}


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


def _json_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _constraint_reason_from_error(exc: Exception) -> str:
    text = str(getattr(exc, "orig", exc))
    for key, reason in _CONSTRAINT_REASON_MAP.items():
        if key in text:
            return reason
    return "db_constraint"


def _price_details(product: Product, new_price: Decimal | None) -> dict[str, Any]:
    return {
        "computed_price": _json_decimal(new_price),
        "min_price": _json_decimal(_as_decimal(getattr(product, "min_price", None))),
        "max_price": _json_decimal(_as_decimal(getattr(product, "max_price", None))),
        "cost_price": _json_decimal(_as_decimal(getattr(product, "cost_price", None))),
        "sale_price": _json_decimal(_as_decimal(getattr(product, "sale_price", None))),
        "preorder_deposit": _json_decimal(_as_decimal(getattr(product, "preorder_deposit", None))),
    }


def _validate_price_update(product: Product, new_price: Decimal) -> tuple[bool, str, dict[str, Any]]:
    details = _price_details(product, new_price)

    if new_price < 0:
        return False, "negative_price", details

    min_price = _as_decimal(getattr(product, "min_price", None))
    if min_price is not None and new_price < min_price:
        return False, "below_min_price", details

    max_price = _as_decimal(getattr(product, "max_price", None))
    if max_price is not None and new_price > max_price:
        return False, "above_max_price", details

    cost_price = _as_decimal(getattr(product, "cost_price", None))
    enable_dumping = bool(getattr(product, "enable_price_dumping", False))
    if not enable_dumping and cost_price is not None and new_price < cost_price:
        return False, "below_cost_price", details

    sale_price = _as_decimal(getattr(product, "sale_price", None))
    if sale_price is not None and new_price < sale_price:
        return False, "below_sale_price", details

    preorder_deposit = _as_decimal(getattr(product, "preorder_deposit", None))
    if preorder_deposit is not None and new_price < preorder_deposit:
        return False, "below_preorder_deposit", details

    return True, "ok", details


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
    rule_id: int | None = None,
) -> RepricingRun:
    now = datetime.utcnow()
    run = RepricingRun(
        company_id=company_id,
        status="running",
        started_at=now,
        triggered_by_user_id=triggered_by_user_id,
        request_id=request_id,
    )
    if rule_id is not None:
        run.rule_id = rule_id
    db.add(run)
    await db.flush()

    rules_stmt = (
        select(RepricingRule)
        .where(
            RepricingRule.company_id == company_id,
            RepricingRule.enabled.is_(True),
            RepricingRule.is_active.is_(True),
        )
        .order_by(RepricingRule.id.asc())
    )
    if rule_id is not None:
        rules_stmt = rules_stmt.where(RepricingRule.id == rule_id)

    rules = (await db.execute(rules_stmt)).scalars().all()

    processed = 0
    changed = 0
    skipped = 0
    failed = 0
    last_error = None
    processed_products: set[int] = set()
    updated_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []

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
                skipped += 1
                skipped_items.append(
                    {
                        "product_id": product.id,
                        "reason": "no_change",
                        **_price_details(product, None),
                    }
                )
                continue

            ok, skip_reason, details = _validate_price_update(product, new_price)
            if not ok:
                item = _build_run_item(
                    run_id=run.id,
                    product=product,
                    old_price=old_price,
                    new_price=new_price,
                    reason=skip_reason,
                    status="skipped",
                )
                db.add(item)
                skipped += 1
                skipped_items.append(
                    {
                        "product_id": product.id,
                        "reason": skip_reason,
                        **details,
                    }
                )
                continue

            try:
                async with db.begin_nested():
                    if not dry_run:
                        product.set_price_guarded(new_price, update_timestamps=True, respect_bounds=True)

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
                    await db.flush()
            except IntegrityError as exc:
                skip_reason = _constraint_reason_from_error(exc)
                last_error = last_error or str(exc)
                item = _build_run_item(
                    run_id=run.id,
                    product=product,
                    old_price=old_price,
                    new_price=new_price,
                    reason=skip_reason,
                    status="skipped",
                    error=str(exc),
                )
                db.add(item)
                skipped += 1
                skipped_items.append(
                    {
                        "product_id": product.id,
                        "reason": skip_reason,
                        **details,
                    }
                )
                continue
            except Exception as exc:
                failed += 1
                last_error = str(exc)
                item = _build_run_item(
                    run_id=run.id,
                    product=product,
                    old_price=old_price,
                    new_price=None,
                    reason="error",
                    status="failed",
                    error=str(exc),
                )
                db.add(item)
                continue

            changed += 1
            updated_items.append(
                {
                    "product_id": product.id,
                    "old_price": _json_decimal(old_price),
                    "new_price": _json_decimal(new_price),
                    "reason": "dry_run" if dry_run else "repriced",
                }
            )

    run.processed = processed
    run.changed = changed
    run.failed = failed
    run.last_error = last_error
    run.stats = {
        "updated": updated_items,
        "skipped": skipped_items,
        "skipped_count": skipped,
    }
    run.finished_at = datetime.utcnow()
    run.status = "failed" if failed else "done"

    return run


def _candidate_items_from_run(run: RepricingRun) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if run.items:
        for item in run.items:
            if item.new_price is None:
                continue
            candidates.append(
                {
                    "product_id": item.product_id,
                    "old_price": item.old_price,
                    "new_price": item.new_price,
                    "reason": item.reason or "repricing",
                }
            )
        return candidates
    for diff in run.diffs:
        if diff.new_price is None:
            continue
        candidates.append(
            {
                "product_id": diff.product_id,
                "old_price": diff.old_price,
                "new_price": diff.new_price,
                "reason": diff.reason or "repricing",
            }
        )
    return candidates


def _has_apply_results(run: RepricingRun) -> bool:
    apply_reasons = {"apply", "apply_failed", "dry_run", "missing_mapping"}
    for item in run.items or []:
        if item.status in {"ok", "dry_run"}:
            return True
        if item.status == "failed" and item.reason in {"apply_failed", "missing_mapping"}:
            return True
        if item.reason in apply_reasons:
            return True
    return False


async def apply_repricing_run_to_kaspi(
    db: AsyncSession,
    *,
    run_id: int,
    company_id: int,
    dry_run: bool = False,
    api_key: str | None = None,
    base_url: str | None = None,
    resolve_credentials: bool = True,
) -> RepricingRun:
    result = await db.execute(
        select(RepricingRun)
        .where(RepricingRun.id == run_id, RepricingRun.company_id == company_id)
        .options(selectinload(RepricingRun.items), selectinload(RepricingRun.diffs))
    )
    run = result.scalar_one_or_none()
    if not run:
        raise NotFoundError("Run not found", "RUN_NOT_FOUND")

    if _has_apply_results(run):
        return run

    if not dry_run:
        token = api_key
        url = base_url
        if resolve_credentials:
            company = await db.get(Company, company_id)
            if company is not None and token is None:
                token = company.kaspi_api_key
            from app.core.config import settings

            if token is None:
                token = settings.KASPI_API_TOKEN
            if url is None:
                url = settings.KASPI_API_URL
        if not token or not url:
            raise SmartSellValidationError(
                "Kaspi API is not configured",
                "KASPI_NOT_CONFIGURED",
                http_status=422,
            )

    candidates = _candidate_items_from_run(run)
    product_ids = {c.get("product_id") for c in candidates if c.get("product_id")}
    products = {}
    if product_ids:
        product_rows = (
            (await db.execute(select(Product).where(Product.company_id == company_id, Product.id.in_(product_ids))))
            .scalars()
            .all()
        )
        products = {p.id: p for p in product_rows}

    processed = 0
    changed = 0
    failed = 0
    last_error = None

    updates: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []

    for item in candidates:
        processed += 1
        product_id = item.get("product_id")
        product = products.get(product_id)
        mapping = None
        if product is not None:
            mapping = product.kaspi_product_id or product.sku

        if not mapping:
            run_item = RepricingRunItem(
                run_id=run.id,
                product_id=product_id,
                old_price=item.get("old_price"),
                new_price=item.get("new_price"),
                reason="missing_mapping",
                status="failed",
                error="missing_mapping",
            )
            db.add(run_item)
            failed += 1
            last_error = last_error or "missing_mapping"
            continue

        prepared.append(
            {
                "product_id": product_id,
                "mapping": mapping,
                "old_price": item.get("old_price"),
                "new_price": item.get("new_price"),
                "reason": item.get("reason"),
            }
        )

    if dry_run:
        for item in prepared:
            db.add(
                RepricingRunItem(
                    run_id=run.id,
                    product_id=item.get("product_id"),
                    old_price=item.get("old_price"),
                    new_price=item.get("new_price"),
                    reason=item.get("reason") or "dry_run",
                    status="dry_run",
                )
            )
            changed += 1
    else:
        for item in prepared:
            updates.append(
                {
                    "product_id": item.get("product_id"),
                    "mapping": item.get("mapping"),
                    "new_price": item.get("new_price"),
                }
            )

        if updates:
            try:
                from app.integrations.marketplaces.kaspi.pricing import apply_price_updates

                results = await apply_price_updates(
                    company_id=company_id,
                    updates=updates,
                    api_key=token,
                    base_url=url,
                )
            except Exception as exc:
                results = []
                last_error = str(exc)
                failed += len(updates)
                for item in updates:
                    db.add(
                        RepricingRunItem(
                            run_id=run.id,
                            product_id=item.get("product_id"),
                            old_price=None,
                            new_price=item.get("new_price"),
                            reason="apply_failed",
                            status="failed",
                            error=str(exc),
                        )
                    )
            else:
                by_product = {r.get("product_id"): r for r in results if r.get("product_id")}
                for item in updates:
                    product_id = item.get("product_id")
                    result = by_product.get(product_id, {})
                    ok = bool(result.get("ok", False))
                    error = result.get("error") if not ok else None
                    status = "ok" if ok else "failed"
                    reason = "apply" if ok else "apply_failed"
                    if ok:
                        changed += 1
                    else:
                        failed += 1
                        last_error = last_error or error or "apply_failed"
                    db.add(
                        RepricingRunItem(
                            run_id=run.id,
                            product_id=product_id,
                            old_price=None,
                            new_price=item.get("new_price"),
                            reason=reason,
                            status=status,
                            error=error,
                        )
                    )

    run.processed = processed
    run.changed = changed
    run.failed = failed
    run.last_error = last_error
    run.finished_at = datetime.utcnow()
    run.status = "failed" if failed else "done"

    await db.commit()
    await db.refresh(run)
    return run
