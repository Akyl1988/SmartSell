"""Compute Kaspi preorder indicator from Kaspi data with tenant-safe fallback."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.kaspi_stock_truth import compute_kaspi_stock_truth


async def compute_kaspi_preorder_candidate(
    db: AsyncSession,
    *,
    company_id: int,
    sku: str | None = None,
    merchant_uid: str | None = None,
    offer_id: str | None = None,
    product_id: int | None = None,
) -> dict[str, Any]:
    if product_id is None:
        return {"preorder_candidate": None, "source": "no_data"}

    truth = await compute_kaspi_stock_truth(
        db,
        company_id=company_id,
        product_id=product_id,
        merchant_uid=merchant_uid,
    )

    if truth.source == "offer":
        if truth.kaspi_offer_pre_order is True:
            return {"preorder_candidate": True, "source": "kaspi_offer.pre_order"}
        if truth.kaspi_offer_stock_count is not None:
            return {
                "preorder_candidate": truth.kaspi_offer_stock_count <= 0,
                "source": "kaspi_offer.stock_count",
            }

    if truth.source == "catalog" and truth.kaspi_catalog_qty is not None:
        return {"preorder_candidate": truth.kaspi_catalog_qty <= 0, "source": "kaspi_catalog.qty"}

    if truth.source == "local" and truth.local_effective_stock is not None:
        return {"preorder_candidate": truth.local_effective_stock <= 0, "source": "smartsell_stock"}

    return {"preorder_candidate": None, "source": "no_data"}
