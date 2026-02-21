"""Compute Kaspi preorder indicator from Kaspi data with tenant-safe fallback."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_offer import KaspiOffer
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


async def _effective_stock(db: AsyncSession, *, company_id: int, product: Product) -> int:
    stmt = (
        select(
            func.count(ProductStock.id),
            func.coalesce(func.sum(ProductStock.quantity - ProductStock.reserved_quantity), 0),
        )
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(ProductStock.product_id == product.id)
        .where(Warehouse.company_id == company_id)
        .where(Warehouse.is_archived.is_(False))
        .where(Warehouse.is_active.is_(True))
    )
    count, total = (await db.execute(stmt)).one()
    if count and int(count) > 0:
        return int(total or 0)
    return max(0, int(product.stock_quantity or 0) - int(product.reserved_quantity or 0))


async def compute_kaspi_preorder_candidate(
    db: AsyncSession,
    *,
    company_id: int,
    sku: str | None = None,
    merchant_uid: str | None = None,
    offer_id: str | None = None,
    product_id: int | None = None,
) -> dict[str, Any]:
    offer = None
    if sku and merchant_uid:
        offer_stmt = select(KaspiOffer).where(
            KaspiOffer.company_id == company_id,
            KaspiOffer.sku == sku,
            KaspiOffer.merchant_uid == merchant_uid,
        )
        offer = (await db.execute(offer_stmt)).scalars().first()

    if offer is not None:
        pre_order = _as_bool(offer.pre_order)
        if pre_order is True:
            return {"preorder_candidate": True, "source": "kaspi_offer.pre_order"}
        if offer.stock_specified and offer.stock_count is not None:
            return {
                "preorder_candidate": int(offer.stock_count) <= 0,
                "source": "kaspi_offer.stock_count",
            }

    catalog = None
    if offer_id:
        catalog_stmt = select(KaspiCatalogProduct).where(
            KaspiCatalogProduct.company_id == company_id,
            KaspiCatalogProduct.offer_id == offer_id,
        )
        catalog = (await db.execute(catalog_stmt)).scalars().first()
    elif sku:
        catalog_stmt = select(KaspiCatalogProduct).where(
            KaspiCatalogProduct.company_id == company_id,
            KaspiCatalogProduct.sku == sku,
        )
        catalog = (await db.execute(catalog_stmt)).scalars().first()

    if catalog is not None and catalog.qty is not None:
        return {"preorder_candidate": int(catalog.qty) <= 0, "source": "kaspi_catalog.qty"}

    product = None
    if product_id is not None:
        product = (
            (await db.execute(select(Product).where(Product.id == product_id, Product.company_id == company_id)))
            .scalars()
            .first()
        )
    elif sku:
        product = (
            (await db.execute(select(Product).where(Product.company_id == company_id, Product.sku == sku)))
            .scalars()
            .first()
        )

    if product is None:
        return {"preorder_candidate": None, "source": "no_data"}

    effective_stock = await _effective_stock(db, company_id=company_id, product=product)
    return {"preorder_candidate": effective_stock <= 0, "source": "smartsell_stock"}
