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
    "start": PlanCatalogEntry(plan_id="start", display_name="Start", price=Decimal("0.00")),
    "business": PlanCatalogEntry(plan_id="business", display_name="Business", price=Decimal("0.00")),
    "pro": PlanCatalogEntry(plan_id="pro", display_name="Pro", price=Decimal("0.00")),
}

PLAN_ALIASES: dict[str, str] = {
    "start": "start",
    "trial": "start",
    "basic": "business",
    "business": "business",
    "pro": "pro",
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


__all__ = [
    "PlanCatalogEntry",
    "PLAN_CATALOG",
    "PLAN_ALIASES",
    "normalize_plan_id",
    "is_canonical_plan_id",
    "get_plan",
    "get_plan_display_name",
    "iter_plan_ids",
]
