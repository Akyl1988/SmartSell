from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kaspi_mc_session import KaspiMcSession
from app.models.kaspi_offer import KaspiOffer

MC_BASE_URL = "https://mc.shop.kaspi.kz"
MC_OFFERS_PATH = "/bff/offer-view/list"
MC_CITY_ID_ALMATY = "750000000"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_city_price(item: dict[str, Any]) -> tuple[float | None, float | None]:
    city_prices = item.get("cityPrices")
    target = None
    if isinstance(city_prices, list):
        for entry in city_prices:
            if isinstance(entry, dict) and str(entry.get("cityId")) == MC_CITY_ID_ALMATY:
                target = entry
                break
        if not target and city_prices:
            target = city_prices[0] if isinstance(city_prices[0], dict) else None
    elif isinstance(city_prices, dict):
        if "cityId" in city_prices:
            target = city_prices
        elif MC_CITY_ID_ALMATY in city_prices:
            target = city_prices.get(MC_CITY_ID_ALMATY)

    if isinstance(target, dict):
        value = _to_float(target.get("value"))
        oldprice = _to_float(target.get("oldprice") or target.get("oldPrice"))
        return value, oldprice
    return None, None


def normalize_mc_offer(item: dict[str, Any]) -> dict[str, Any]:
    sku = item.get("sku")
    master_sku = item.get("masterSku")
    title = item.get("title")

    price, old_price = _extract_city_price(item)

    if price is not None and price <= 0:
        price = None

    if price is None:
        price = _to_float(item.get("minPrice"))
    if price is None:
        price = _to_float(item.get("maxPrice"))
    if price is None:
        range_price = item.get("rangePrice") or {}
        if isinstance(range_price, dict):
            price = _to_float(range_price.get("MIN") or range_price.get("min") or range_price.get("MAX"))

    if old_price is None:
        old_price = 0.0

    stock_count = None
    pre_order = None
    stock_specified = None
    availabilities = item.get("availabilities") or []
    if isinstance(availabilities, list) and availabilities:
        first = availabilities[0] if isinstance(availabilities[0], dict) else None
        if first:
            stock_count = first.get("stockCount") or first.get("quantity") or first.get("count")
            pre_order = first.get("preOrder") if "preOrder" in first else first.get("pre_order")
            stock_specified = first.get("stockSpecified") if "stockSpecified" in first else first.get("stock_specified")

    return {
        "sku": sku,
        "master_sku": master_sku,
        "title": title,
        "price": price,
        "old_price": old_price,
        "stock_count": stock_count,
        "pre_order": pre_order if pre_order is None else bool(pre_order),
        "stock_specified": stock_specified if stock_specified is None else bool(stock_specified),
        "raw": item,
    }


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "data", "offers", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
    if isinstance(payload, list):
        return [v for v in payload if isinstance(v, dict)]
    return []


def _extract_total(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("total", "totalCount", "count"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
    return None


async def sync_kaspi_mc_offers(
    session: AsyncSession,
    *,
    company_id: int,
    merchant_uid: str,
    cookies: str,
    x_auth_version: int = 3,
    page_limit: int = 100,
    max_pages: int = 500,
) -> dict[str, Any]:
    rows_ok = 0
    rows_failed = 0
    rows_total = 0
    errors: list[dict[str, Any]] = []
    total_hint = None
    now = datetime.utcnow()

    headers = {
        "Accept": "application/json",
        "X-Auth-Version": str(x_auth_version or 3),
        "Cookie": cookies,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for page in range(0, max_pages):
            resp = await client.get(
                f"{MC_BASE_URL}{MC_OFFERS_PATH}",
                headers=headers,
                params={"m": merchant_uid, "p": page, "l": page_limit, "a": "true"},
            )
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
            items = _extract_items(payload)
            if total_hint is None:
                total_hint = _extract_total(payload)
            if not items:
                break

            for item in items:
                try:
                    normalized = normalize_mc_offer(item)
                    sku = normalized.get("sku")
                    if not sku:
                        rows_failed += 1
                        errors.append({"error": "missing_sku"})
                        continue

                    stmt = (
                        insert(KaspiOffer)
                        .values(
                            company_id=company_id,
                            merchant_uid=merchant_uid,
                            sku=str(sku),
                            master_sku=normalized.get("master_sku"),
                            title=normalized.get("title"),
                            price=normalized.get("price"),
                            old_price=normalized.get("old_price"),
                            stock_count=normalized.get("stock_count"),
                            pre_order=normalized.get("pre_order"),
                            stock_specified=normalized.get("stock_specified"),
                            raw=normalized.get("raw") or {},
                            updated_at=now,
                            created_at=now,
                        )
                        .on_conflict_do_update(
                            index_elements=["company_id", "merchant_uid", "sku"],
                            set_={
                                "master_sku": normalized.get("master_sku"),
                                "title": normalized.get("title"),
                                "price": normalized.get("price"),
                                "old_price": normalized.get("old_price"),
                                "stock_count": normalized.get("stock_count"),
                                "pre_order": normalized.get("pre_order"),
                                "stock_specified": normalized.get("stock_specified"),
                                "raw": normalized.get("raw") or {},
                                "updated_at": now,
                            },
                        )
                    )
                    await session.execute(stmt)
                    rows_ok += 1
                except Exception as exc:  # pragma: no cover - defensive
                    rows_failed += 1
                    errors.append({"sku": item.get("sku"), "error": str(exc)})

            rows_total += len(items)
            if len(items) < page_limit and (total_hint is None or rows_total >= total_hint):
                break

    await session.commit()

    mc_row = (
        (
            await session.execute(
                sa.select(KaspiMcSession).where(
                    KaspiMcSession.company_id == company_id,
                    KaspiMcSession.merchant_uid == merchant_uid,
                )
            )
        )
        .scalars()
        .first()
    )
    if mc_row:
        mc_row.last_used_at = now
        mc_row.last_error = None
        mc_row.last_error_code = None
        mc_row.last_error_at = None
        await session.commit()

    return {
        "rows_total": total_hint if total_hint is not None else rows_total,
        "rows_ok": rows_ok,
        "rows_failed": rows_failed,
        "errors": errors,
    }


async def mark_mc_session_error(
    session: AsyncSession,
    *,
    company_id: int,
    merchant_uid: str,
    error_code: str,
    error: str | None = None,
) -> None:
    row = (
        (
            await session.execute(
                sa.select(KaspiMcSession).where(
                    KaspiMcSession.company_id == company_id,
                    KaspiMcSession.merchant_uid == merchant_uid,
                )
            )
        )
        .scalars()
        .first()
    )
    if row:
        row.last_error = error or error_code
        row.last_error_code = error_code
        row.last_error_at = datetime.utcnow()
        await session.commit()
