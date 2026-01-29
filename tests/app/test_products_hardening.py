import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_product_duplicate_sku_same_tenant_returns_409(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    first = await async_client.post(
        "/api/v1/products",
        headers=company_a_admin_headers,
        json={
            "name": "Product A",
            "slug": "product-a",
            "sku": "SKU-001",
            "price": "10.00",
            "stock_quantity": 5,
        },
    )
    assert first.status_code == 200, first.text

    second = await async_client.post(
        "/api/v1/products",
        headers=company_a_admin_headers,
        json={
            "name": "Product A2",
            "slug": "product-a2",
            "sku": "SKU-001",
            "price": "12.00",
            "stock_quantity": 3,
        },
    )
    assert second.status_code == 409
    assert second.json().get("detail") == "Product with this SKU already exists"


@pytest.mark.asyncio
async def test_product_duplicate_sku_cross_tenant_ok(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
    company_b_admin_headers,
):
    first = await async_client.post(
        "/api/v1/products",
        headers=company_a_admin_headers,
        json={
            "name": "Tenant A",
            "slug": "tenant-a",
            "sku": "SKU-002",
            "price": "5.00",
            "stock_quantity": 1,
        },
    )
    assert first.status_code == 200, first.text

    second = await async_client.post(
        "/api/v1/products",
        headers=company_b_admin_headers,
        json={
            "name": "Tenant B",
            "slug": "tenant-b",
            "sku": "SKU-002",
            "price": "7.00",
            "stock_quantity": 2,
        },
    )
    assert second.status_code == 200, second.text


@pytest.mark.asyncio
async def test_product_negative_price_or_stock_422(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    bad_price = await async_client.post(
        "/api/v1/products",
        headers=company_a_admin_headers,
        json={
            "name": "Bad Price",
            "slug": "bad-price",
            "sku": "SKU-003",
            "price": "-1.00",
            "stock_quantity": 0,
        },
    )
    assert bad_price.status_code == 422

    bad_stock = await async_client.post(
        "/api/v1/products",
        headers=company_a_admin_headers,
        json={
            "name": "Bad Stock",
            "slug": "bad-stock",
            "sku": "SKU-004",
            "price": "1.00",
            "stock_quantity": -1,
        },
    )
    assert bad_stock.status_code == 422
