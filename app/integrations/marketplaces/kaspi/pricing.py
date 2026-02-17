"""Kaspi pricing integration stub for repricing runs."""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)


def apply_price_updates(company_id: int, updates: Iterable[dict]) -> dict:
    """Apply price updates to Kaspi (stub for dev/test).

    updates: iterable of {"product_id": int, "new_price": Decimal}
    """
    count = 0
    for update in updates:
        _ = update
        count += 1
    logger.info("Kaspi pricing stub applied updates: company_id=%s count=%s", company_id, count)
    return {"applied": count}
