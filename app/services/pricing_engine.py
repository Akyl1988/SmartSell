"""Async-friendly repricing computation helpers (no sync DB access)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any


@dataclass(frozen=True)
class RuleConfig:
    min_price: Decimal | None
    max_price: Decimal | None
    step: Decimal | None
    undercut: Decimal | None
    cooldown_seconds: int | None
    max_delta_percent: Decimal | None


@dataclass(frozen=True)
class PricingDecision:
    product_id: int
    old_price: Decimal | None
    new_price: Decimal | None
    reason: str
    meta: dict[str, Any]


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _clamp_price(
    value: Decimal,
    min_a: Decimal | None,
    max_a: Decimal | None,
    min_b: Decimal | None,
    max_b: Decimal | None,
) -> tuple[Decimal, dict[str, Any]]:
    meta: dict[str, Any] = {}
    min_candidates = [v for v in (min_a, min_b) if v is not None]
    max_candidates = [v for v in (max_a, max_b) if v is not None]
    effective_min = max(min_candidates) if min_candidates else None
    effective_max = min(max_candidates) if max_candidates else None

    out = value
    if effective_min is not None and out < effective_min:
        out = effective_min
        meta["clamped_min"] = True
    if effective_max is not None and out > effective_max:
        out = effective_max
        meta["clamped_max"] = True
    return out, meta


def _apply_step(delta: Decimal, step: Decimal | None) -> Decimal:
    if step is None:
        return delta
    if step <= 0:
        return delta
    multiplier = (delta / step).to_integral_value(rounding=ROUND_FLOOR)
    return step * multiplier


def evaluate_product(product: Any, rule: RuleConfig, *, now: datetime | None = None) -> PricingDecision:
    now = now or datetime.utcnow()
    product_id = int(getattr(product, "id", 0) or 0)
    old_price = _as_decimal(getattr(product, "price", None))

    if old_price is None:
        return PricingDecision(product_id, None, None, "missing_price", {})

    repriced_at = getattr(product, "repriced_at", None)
    if rule.cooldown_seconds and repriced_at:
        elapsed = now - repriced_at
        if elapsed < timedelta(seconds=int(rule.cooldown_seconds)):
            return PricingDecision(product_id, old_price, None, "cooldown", {})

    base_delta = _as_decimal(rule.undercut) or _as_decimal(rule.step)
    if base_delta is None or base_delta <= 0:
        return PricingDecision(product_id, old_price, None, "no_delta", {})

    delta = _apply_step(base_delta, _as_decimal(rule.step))
    if delta <= 0:
        return PricingDecision(product_id, old_price, None, "delta_below_step", {})

    meta: dict[str, Any] = {"delta": str(delta)}

    if rule.max_delta_percent is not None and rule.max_delta_percent > 0:
        max_delta = old_price * _as_decimal(rule.max_delta_percent) / Decimal("100")
        max_delta = _quantize_money(max_delta)
        if max_delta <= 0:
            return PricingDecision(product_id, old_price, None, "max_delta_zero", {})
        if delta > max_delta:
            delta = max_delta
            meta["delta_limited"] = True

    target = _quantize_money(old_price - delta)
    if target < 0:
        target = Decimal("0.00")

    clamped, clamp_meta = _clamp_price(
        target,
        _as_decimal(getattr(product, "min_price", None)),
        _as_decimal(getattr(product, "max_price", None)),
        _as_decimal(rule.min_price),
        _as_decimal(rule.max_price),
    )
    meta.update(clamp_meta)

    if clamped == old_price:
        return PricingDecision(product_id, old_price, None, "no_change", meta)

    return PricingDecision(product_id, old_price, clamped, "repricing", meta)
