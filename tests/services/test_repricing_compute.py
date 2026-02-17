from __future__ import annotations

from decimal import Decimal

from app.models.repricing import RepricingRule
from app.services.repricing import compute_new_price


def _rule(**overrides) -> RepricingRule:
    data = {
        "company_id": 1,
        "name": "rule",
        "enabled": True,
        "is_active": True,
        "scope_type": "all",
        "scope_value": None,
    }
    data.update(overrides)
    return RepricingRule(**data)


def test_compute_new_price_step_down():
    rule = _rule(step=Decimal("5.00"))
    assert compute_new_price(Decimal("100.00"), rule) == Decimal("95.00")


def test_compute_new_price_clamps_min():
    rule = _rule(step=Decimal("5.00"), min_price=Decimal("10.00"))
    assert compute_new_price(Decimal("12.00"), rule) == Decimal("10.00")


def test_compute_new_price_clamps_max():
    rule = _rule(step=Decimal("5.00"), max_price=Decimal("200.00"))
    assert compute_new_price(Decimal("250.00"), rule) == Decimal("200.00")


def test_compute_new_price_rounding_floor():
    rule = _rule(step=Decimal("3.00"), rounding_mode="floor")
    assert compute_new_price(Decimal("10.00"), rule) == Decimal("6.00")


def test_compute_new_price_rounding_nearest():
    rule = _rule(step=Decimal("2.00"), rounding_mode="nearest")
    assert compute_new_price(Decimal("11.00"), rule) == Decimal("10.00")


def test_compute_new_price_no_change():
    rule = _rule(step=None, min_price=Decimal("10.00"), max_price=Decimal("10.00"))
    assert compute_new_price(Decimal("10.00"), rule) is None
