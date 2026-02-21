from __future__ import annotations

import pytest

from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_offer import KaspiOffer
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse
from app.services.kaspi_preorder_indicator import compute_kaspi_preorder_candidate

pytestmark = pytest.mark.asyncio


async def _seed_product(async_db_session, *, company_id: int, sku: str, stock: int) -> Product:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.commit()

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


async def test_preorder_candidate_stock_zero(async_db_session):
    company = Company(id=1001, name="Company 1001")
    async_db_session.add(company)
    await async_db_session.commit()

    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-1", stock=0)

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="m1",
        sku="SKU-1",
        stock_count=0,
        stock_specified=True,
        pre_order=False,
        raw={},
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    result = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=1001,
        sku="SKU-1",
        merchant_uid="m1",
        product_id=product.id,
    )
    assert result["preorder_candidate"] is True
    assert result["source"] == "kaspi_offer.stock_count"


async def test_preorder_candidate_stock_positive(async_db_session):
    company = Company(id=1001, name="Company 1001")
    async_db_session.add(company)
    await async_db_session.commit()

    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-2", stock=5)

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="m1",
        sku="SKU-2",
        stock_count=5,
        stock_specified=True,
        pre_order=False,
        raw={},
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    result = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=1001,
        sku="SKU-2",
        merchant_uid="m1",
        product_id=product.id,
    )
    assert result["preorder_candidate"] is False
    assert result["source"] == "kaspi_offer.stock_count"


async def test_preorder_candidate_fallback_no_warehouses(async_db_session):
    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-3", stock=0)

    result = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=1001,
        sku=product.sku,
        product_id=product.id,
    )
    assert result["preorder_candidate"] is True
    assert result["source"] == "smartsell_stock"


async def test_preorder_candidate_tenant_isolation(async_db_session):
    companies = [Company(id=1001, name="Company 1001"), Company(id=2001, name="Company 2001")]
    async_db_session.add_all(companies)
    await async_db_session.commit()

    product_a = await _seed_product(async_db_session, company_id=1001, sku="SKU-4", stock=0)
    product_b = await _seed_product(async_db_session, company_id=2001, sku="SKU-4", stock=5)

    offer_a = KaspiOffer(
        company_id=1001,
        merchant_uid="m1",
        sku="SKU-4",
        stock_count=0,
        stock_specified=True,
        pre_order=False,
        raw={},
    )
    offer_b = KaspiOffer(
        company_id=2001,
        merchant_uid="m1",
        sku="SKU-4",
        stock_count=5,
        stock_specified=True,
        pre_order=False,
        raw={},
    )
    async_db_session.add_all([offer_a, offer_b])
    await async_db_session.commit()

    result_a = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=1001,
        sku="SKU-4",
        merchant_uid="m1",
        product_id=product_a.id,
    )
    result_b = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=2001,
        sku="SKU-4",
        merchant_uid="m1",
        product_id=product_b.id,
    )
    assert result_a["preorder_candidate"] is True
    assert result_b["preorder_candidate"] is False


async def test_preorder_candidate_skips_offer_without_merchant_uid(async_db_session):
    company = Company(id=1001, name="Company 1001")
    async_db_session.add(company)
    await async_db_session.commit()

    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-10", stock=7)

    offers = [
        KaspiOffer(
            company_id=1001,
            merchant_uid="m1",
            sku="SKU-10",
            stock_count=0,
            stock_specified=True,
            pre_order=False,
            raw={},
        ),
        KaspiOffer(
            company_id=1001,
            merchant_uid="m2",
            sku="SKU-10",
            stock_count=5,
            stock_specified=True,
            pre_order=False,
            raw={},
        ),
    ]
    async_db_session.add_all(offers)
    async_db_session.add(
        KaspiCatalogProduct(
            company_id=1001,
            offer_id="offer-10",
            sku="SKU-10",
            qty=7,
            raw={},
        )
    )
    await async_db_session.commit()

    result = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=1001,
        sku="SKU-10",
        product_id=product.id,
    )

    assert result["preorder_candidate"] is False
    assert result["source"] == "kaspi_catalog.qty"


async def test_preorder_candidate_uses_offer_with_merchant_uid(async_db_session):
    company = Company(id=1001, name="Company 1001")
    async_db_session.add(company)
    await async_db_session.commit()

    product = await _seed_product(async_db_session, company_id=1001, sku="SKU-11", stock=9)

    offers = [
        KaspiOffer(
            company_id=1001,
            merchant_uid="m1",
            sku="SKU-11",
            stock_count=0,
            stock_specified=True,
            pre_order=False,
            raw={},
        ),
        KaspiOffer(
            company_id=1001,
            merchant_uid="m2",
            sku="SKU-11",
            stock_count=4,
            stock_specified=True,
            pre_order=False,
            raw={},
        ),
    ]
    async_db_session.add_all(offers)
    async_db_session.add(
        KaspiCatalogProduct(
            company_id=1001,
            offer_id="offer-11",
            sku="SKU-11",
            qty=9,
            raw={},
        )
    )
    await async_db_session.commit()

    result = await compute_kaspi_preorder_candidate(
        async_db_session,
        company_id=1001,
        sku="SKU-11",
        merchant_uid="m1",
        product_id=product.id,
    )

    assert result["preorder_candidate"] is True
    assert result["source"] == "kaspi_offer.stock_count"
