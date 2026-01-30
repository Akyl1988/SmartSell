from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kaspi_offer import KaspiOffer


def build_goods_import_payload(
    offers: list[KaspiOffer],
    *,
    include_price: bool = False,
    include_stock: bool = False,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    sorted_offers = sorted(offers, key=lambda item: (item.sku or "", item.id))
    for offer in sorted_offers:
        sku = (offer.sku or "").strip() or f"SKU-{offer.id}"
        name = (offer.title or "").strip() or sku
        item: dict[str, Any] = {
            "sku": sku,
            "name": name,
        }
        if include_price and offer.price is not None:
            item["price"] = float(offer.price)
        if include_stock and offer.stock_count is not None:
            item["stockCount"] = int(offer.stock_count)
        payload.append(item)
    return payload


def build_payload_json(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def compute_payload_hash(payload_json: str) -> str:
    return sha256(payload_json.encode("utf-8")).hexdigest()


async def load_offers_payload(
    session: AsyncSession,
    *,
    company_id: int,
    merchant_uid: str,
    include_price: bool = False,
    include_stock: bool = False,
) -> list[dict[str, Any]]:
    result = await session.execute(
        sa.select(KaspiOffer)
        .where(
            KaspiOffer.company_id == company_id,
            KaspiOffer.merchant_uid == merchant_uid,
        )
        .order_by(KaspiOffer.sku.asc())
    )
    offers = result.scalars().all()
    return build_goods_import_payload(offers, include_price=include_price, include_stock=include_stock)
