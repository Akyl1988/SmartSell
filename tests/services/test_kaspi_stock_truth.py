from __future__ import annotations

import pytest

from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_offer import KaspiOffer
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse
from app.services.kaspi_stock_truth import compute_kaspi_stock_truth

pytestmark = pytest.mark.asyncio


async def _ensure_company(async_db_session, company_id: int) -> None:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.commit()


async def _seed_product(async_db_session, *, company_id: int, sku: str, stock: int) -> Product:
    await _ensure_company(async_db_session, company_id)
    product = Product(
        company_id=company_id,
        name=f"Product {sku}",
        slug=f"product-{sku.lower()}",
        sku=sku,
        price=100,
        stock_quantity=stock,
    )
    async_db_session.add(product)
    await async_db_session.commit()
    await async_db_session.refresh(product)
    return product


async def _seed_stock(async_db_session, *, company_id: int, product: Product, quantity: int) -> None:
    warehouse = Warehouse(company_id=company_id, name=f"WH-{company_id}", is_main=True)
    async_db_session.add(warehouse)
    await async_db_session.commit()
    await async_db_session.refresh(warehouse)

    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=quantity, reserved_quantity=0)
    async_db_session.add(stock)
    await async_db_session.commit()


async def test_kaspi_stock_truth_offer_path(async_db_session):
    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-1", stock=5)

    async_db_session.add(
        KaspiOffer(
            company_id=1001,
            merchant_uid="m1",
            sku="SKU-1",
            stock_count=0,
            stock_specified=True,
            pre_order=False,
            raw={},
        )
    )
    await async_db_session.commit()

    truth = await compute_kaspi_stock_truth(
        async_db_session,
        company_id=1001,
        product_id=product.id,
        merchant_uid="m1",
    )

    assert truth.source == "offer"
    assert truth.kaspi_offer_pre_order is False
    assert truth.kaspi_offer_stock_count == 0
    assert truth.preorder_candidate is True


async def test_kaspi_stock_truth_catalog_path(async_db_session):
    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-2", stock=5)

    async_db_session.add(
        KaspiCatalogProduct(
            company_id=1001,
            offer_id="offer-2",
            sku="SKU-2",
            qty=3,
            raw={},
        )
    )
    await async_db_session.commit()

    truth = await compute_kaspi_stock_truth(
        async_db_session,
        company_id=1001,
        product_id=product.id,
    )

    assert truth.source == "catalog"
    assert truth.kaspi_catalog_qty == 3
    assert truth.preorder_candidate is False


async def test_kaspi_stock_truth_local_fallback_tenant_safe(async_db_session):
    product_a = await _seed_product(async_db_session, company_id=1001, sku="SKU-3", stock=0)
    product_b = await _seed_product(async_db_session, company_id=2001, sku="SKU-3", stock=10)

    await _seed_stock(async_db_session, company_id=1001, product=product_a, quantity=0)
    await _seed_stock(async_db_session, company_id=2001, product=product_b, quantity=5)

    truth_a = await compute_kaspi_stock_truth(
        async_db_session,
        company_id=1001,
        product_id=product_a.id,
    )
    truth_b = await compute_kaspi_stock_truth(
        async_db_session,
        company_id=2001,
        product_id=product_b.id,
    )

    assert truth_a.source == "local"
    assert truth_a.local_effective_stock == 0
    assert truth_a.preorder_candidate is True

    assert truth_b.source == "local"
    assert truth_b.local_effective_stock == 5
    assert truth_b.preorder_candidate is False
