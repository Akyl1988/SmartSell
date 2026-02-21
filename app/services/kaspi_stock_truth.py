"""Compute Kaspi stock truth with tenant-safe fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_offer import KaspiOffer
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse

StockSource = Literal["offer", "catalog", "local", "unknown"]


@dataclass(slots=True)
class KaspiStockTruth:
    kaspi_offer_pre_order: bool | None
    kaspi_offer_stock_count: int | None
    kaspi_catalog_qty: int | None
    local_effective_stock: int | None
    preorder_candidate: bool | None
    source: StockSource
    notes: dict[str, Any] | None = None


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


async def compute_kaspi_stock_truth(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    merchant_uid: str | None = None,
) -> KaspiStockTruth:
    kaspi_offer_pre_order = None
    kaspi_offer_stock_count = None
    kaspi_catalog_qty = None
    local_effective_stock = None
    preorder_candidate = None
    source: StockSource = "unknown"

    product = (
        (await db.execute(select(Product).where(Product.id == product_id, Product.company_id == company_id)))
        .scalars()
        .first()
    )
    if product is None:
        return KaspiStockTruth(
            kaspi_offer_pre_order=kaspi_offer_pre_order,
            kaspi_offer_stock_count=kaspi_offer_stock_count,
            kaspi_catalog_qty=kaspi_catalog_qty,
            local_effective_stock=local_effective_stock,
            preorder_candidate=preorder_candidate,
            source=source,
        )

    sku = product.sku

    offer = None
    if sku and merchant_uid:
        offer_stmt = select(KaspiOffer).where(
            KaspiOffer.company_id == company_id,
            KaspiOffer.sku == sku,
            KaspiOffer.merchant_uid == merchant_uid,
        )
        offer = (await db.execute(offer_stmt)).scalars().first()

    if offer is not None:
        kaspi_offer_pre_order = _as_bool(offer.pre_order)
        if offer.stock_count is not None:
            kaspi_offer_stock_count = int(offer.stock_count)
        if kaspi_offer_pre_order is True:
            preorder_candidate = True
            source = "offer"
        elif offer.stock_specified and kaspi_offer_stock_count is not None:
            preorder_candidate = kaspi_offer_stock_count <= 0
            source = "offer"
        if source == "offer":
            return KaspiStockTruth(
                kaspi_offer_pre_order=kaspi_offer_pre_order,
                kaspi_offer_stock_count=kaspi_offer_stock_count,
                kaspi_catalog_qty=kaspi_catalog_qty,
                local_effective_stock=local_effective_stock,
                preorder_candidate=preorder_candidate,
                source=source,
            )

    catalog = None
    if sku:
        catalog_stmt = select(KaspiCatalogProduct).where(
            KaspiCatalogProduct.company_id == company_id,
            KaspiCatalogProduct.sku == sku,
        )
        catalog = (await db.execute(catalog_stmt)).scalars().first()

    if catalog is not None and catalog.qty is not None:
        kaspi_catalog_qty = int(catalog.qty)
        preorder_candidate = kaspi_catalog_qty <= 0
        source = "catalog"
        return KaspiStockTruth(
            kaspi_offer_pre_order=kaspi_offer_pre_order,
            kaspi_offer_stock_count=kaspi_offer_stock_count,
            kaspi_catalog_qty=kaspi_catalog_qty,
            local_effective_stock=local_effective_stock,
            preorder_candidate=preorder_candidate,
            source=source,
        )

    local_effective_stock = await _effective_stock(db, company_id=company_id, product=product)
    preorder_candidate = local_effective_stock <= 0
    source = "local"
    return KaspiStockTruth(
        kaspi_offer_pre_order=kaspi_offer_pre_order,
        kaspi_offer_stock_count=kaspi_offer_stock_count,
        kaspi_catalog_qty=kaspi_catalog_qty,
        local_effective_stock=local_effective_stock,
        preorder_candidate=preorder_candidate,
        source=source,
    )
