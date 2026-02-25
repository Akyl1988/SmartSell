from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PlanCatalogEntry:
    plan_id: str
    display_name: str
    price: Decimal
    currency: str = "KZT"


PLAN_CATALOG: dict[str, PlanCatalogEntry] = {
    "basic": PlanCatalogEntry(plan_id="basic", display_name="Basic", price=Decimal("0.00")),
    "start": PlanCatalogEntry(plan_id="start", display_name="Start", price=Decimal("0.00")),
    "business": PlanCatalogEntry(plan_id="business", display_name="Business", price=Decimal("0.00")),
    "pro": PlanCatalogEntry(plan_id="pro", display_name="Pro", price=Decimal("0.00")),
}

PLAN_ALIASES: dict[str, str] = {
    "start": "start",
    "trial": "start",
    "basic": "basic",
    "business": "business",
    "pro": "pro",
}

FEATURE_MATRIX: dict[str, set[str]] = {
    "basic": {
        "kaspi.orders_list",
        "kaspi.sync_now",
        "kaspi.goods_imports",
        "kaspi.feed_uploads",
        "kaspi.autosync",
    },
    "start": {
        "kaspi.orders_list",
    },
    "business": {
        "kaspi.orders_list",
        "kaspi.sync_now",
        "kaspi.goods_imports",
        "kaspi.feed_uploads",
        "kaspi.autosync",
    },
    "pro": {
        "repricing",
        "preorders",
        "kaspi.orders_list",
        "kaspi.sync_now",
        "kaspi.goods_imports",
        "kaspi.feed_uploads",
        "kaspi.autosync",
    },
}


def normalize_plan_id(raw: str | None, *, default: str | None = "start") -> str | None:
    key = (raw or "").strip().lower()
    if not key:
        return default
    if key in PLAN_ALIASES:
        return PLAN_ALIASES[key]
    if key in PLAN_CATALOG:
        return key
    return default


def is_canonical_plan_id(plan_id: str | None) -> bool:
    return (plan_id or "") in PLAN_CATALOG


def get_plan(plan_id: str | None, *, default: str | None = "start") -> PlanCatalogEntry | None:
    normalized = normalize_plan_id(plan_id, default=default)
    if normalized is None:
        return None
    return PLAN_CATALOG.get(normalized)


def get_plan_display_name(plan_id: str | None, *, default: str | None = "start") -> str:
    plan = get_plan(plan_id, default=default)
    if plan is None:
        return (plan_id or "").strip() or "Start"
    return plan.display_name


def iter_plan_ids() -> Iterable[str]:
    return PLAN_CATALOG.keys()


def list_plans() -> list[dict[str, Decimal | str]]:
    ordered = ["start", "business", "pro"]
    items: list[dict[str, Decimal | str]] = []
    for plan_id in ordered:
        plan = PLAN_CATALOG[plan_id]
        monthly_price = plan.price
        yearly_price = plan.price * Decimal("12")
        items.append(
            {
                "plan_id": plan.plan_id,
                "plan": plan.display_name,
                "currency": plan.currency,
                "monthly_price": monthly_price,
                "yearly_price": yearly_price,
            }
        )
    return items


def get_plan_features(plan_id: str | None) -> set[str]:
    normalized = normalize_plan_id(plan_id) or "start"
    return FEATURE_MATRIX.get(normalized, set())


__all__ = [
    "PlanCatalogEntry",
    "PLAN_CATALOG",
    "PLAN_ALIASES",
    "FEATURE_MATRIX",
    "normalize_plan_id",
    "is_canonical_plan_id",
    "get_plan",
    "get_plan_display_name",
    "iter_plan_ids",
    "list_plans",
    "get_plan_features",
]
