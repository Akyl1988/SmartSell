from __future__ import annotations

from app.core.subscriptions.plan_catalog import get_plan_features, normalize_plan_id

FEATURE_REPRICING = "repricing"
FEATURE_PREORDERS = "preorders"
FEATURE_CAMPAIGNS = "campaigns"
FEATURE_KASPI_ORDERS_LIST = "kaspi.orders_list"
FEATURE_KASPI_SYNC_NOW = "kaspi.sync_now"
FEATURE_KASPI_GOODS_IMPORTS = "kaspi.goods_imports"
FEATURE_KASPI_FEED_UPLOADS = "kaspi.feed_uploads"
FEATURE_KASPI_AUTOSYNC = "kaspi.autosync"

FEATURES_BY_PLAN: dict[str, set[str]] = {
    "start": {
        FEATURE_CAMPAIGNS,
    },
    "basic": {
        FEATURE_CAMPAIGNS,
    },
    "business": {
        FEATURE_CAMPAIGNS,
    },
    "pro": {
        FEATURE_REPRICING,
        FEATURE_PREORDERS,
        FEATURE_CAMPAIGNS,
    },
}


def get_enabled_features_for_plan(plan_code: str | None) -> set[str]:
    normalized = normalize_plan_id(plan_code) or "start"
    from_catalog = set(get_plan_features(normalized))
    from_registry = set(FEATURES_BY_PLAN.get(normalized, set()))
    return from_catalog | from_registry


def is_feature_enabled_for_plan(plan_code: str | None, feature_code: str) -> bool:
    normalized_feature = (feature_code or "").strip().lower()
    if not normalized_feature:
        return False
    return normalized_feature in get_enabled_features_for_plan(plan_code)


__all__ = [
    "FEATURE_REPRICING",
    "FEATURE_PREORDERS",
    "FEATURE_CAMPAIGNS",
    "FEATURE_KASPI_ORDERS_LIST",
    "FEATURE_KASPI_SYNC_NOW",
    "FEATURE_KASPI_GOODS_IMPORTS",
    "FEATURE_KASPI_FEED_UPLOADS",
    "FEATURE_KASPI_AUTOSYNC",
    "FEATURES_BY_PLAN",
    "get_enabled_features_for_plan",
    "is_feature_enabled_for_plan",
]
