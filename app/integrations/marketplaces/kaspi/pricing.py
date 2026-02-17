"""Kaspi pricing integration stub for repricing runs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)


async def apply_price_updates(
    company_id: int,
    updates: Iterable[dict],
    *,
    api_key: str | None,
    base_url: str | None,
) -> list[dict[str, object]]:
    """Apply price updates to Kaspi.

    updates: iterable of {"product_id": int, "mapping": str, "new_price": Decimal}
    """
    update_list = list(updates)
    if not update_list:
        return []
    if not api_key or not base_url:
        raise RuntimeError("Kaspi API not configured")

    url = f"{base_url.rstrip('/')}/products/prices"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload_items = []
    for update in update_list:
        price = update.get("new_price")
        if isinstance(price, Decimal):
            price = str(price)
        payload_items.append({"sku": update.get("mapping"), "price": price})
    payload = {"items": payload_items}

    attempts = 0
    last_exc: Exception | None = None
    while attempts < 2:
        attempts += 1
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, headers=headers, json=payload)
            if 200 <= response.status_code < 300:
                data = None
                try:
                    data = response.json()
                except Exception:
                    data = None
                if not isinstance(data, dict) or "items" not in data:
                    return [{"product_id": u.get("product_id"), "ok": True} for u in update_list]
                by_sku = {item.get("sku"): item for item in data.get("items", []) if isinstance(item, dict)}
                results = []
                for update in update_list:
                    item = by_sku.get(update.get("mapping"), {})
                    errors = item.get("errors") if isinstance(item, dict) else None
                    if errors:
                        results.append({"product_id": update.get("product_id"), "ok": False, "error": str(errors)})
                    else:
                        results.append({"product_id": update.get("product_id"), "ok": True})
                return results
            last_exc = RuntimeError(f"Kaspi API error: {response.status_code}")
        except Exception as exc:
            last_exc = exc
        await asyncio.sleep(0.5 * attempts)

    raise RuntimeError(str(last_exc) if last_exc else "Kaspi API error")
