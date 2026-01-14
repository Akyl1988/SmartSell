"""
Tests for Kaspi catalog products sync MVP.

Scenarios:
1. Sync creates records and list returns them (single company)
2. Repeated sync is idempotent (count doesn't grow)
3. Tenant isolation: company A data not visible to company B
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.models.kaspi_catalog_product import KaspiCatalogProduct


def _fake_products_payload() -> list[dict]:
    """Generate fake Kaspi products response."""
    return [
        {
            "id": "kaspi-product-001",
            "offer_id": "OFFER-001",
            "name": "Test Product 1",
            "sku": "SKU-001",
            "price": 10000,
            "qty": 50,
            "is_active": True,
        },
        {
            "id": "kaspi-product-002",
            "offer_id": "OFFER-002",
            "name": "Test Product 2",
            "sku": "SKU-002",
            "price": 15000,
            "qty": 30,
            "is_active": True,
        },
    ]


@pytest.mark.asyncio
async def test_kaspi_products_sync_creates_and_lists(
    monkeypatch,
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    """
    Test that sync creates product records and list endpoint returns them.

    Scenario:
    1. Mock KaspiService.get_products to return 2 products
    2. Call POST /api/v1/kaspi/products/sync
    3. Verify: inserted=2, updated=0
    4. Call GET /api/v1/kaspi/products
    5. Verify: 2 products returned with correct fields
    """
    from app.services.kaspi_service import KaspiService

    # Mock get_products
    async def fake_get_products(self, *, page=1, page_size=100):  # noqa: ARG001
        return _fake_products_payload()

    monkeypatch.setattr(KaspiService, "get_products", fake_get_products)

    # First sync
    resp = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["ok"] is True
    assert data["company_id"] == 1001
    assert data["fetched"] == 2
    assert data["inserted"] == 2
    assert data["updated"] == 0

    # Verify in database
    res = await async_db_session.execute(
        sa.select(sa.func.count(KaspiCatalogProduct.id)).where(KaspiCatalogProduct.company_id == 1001)
    )
    count = res.scalar_one()
    assert count == 2

    # Get list
    list_resp = await async_client.get("/api/v1/kaspi/products", headers=company_a_admin_headers)
    assert list_resp.status_code == 200, list_resp.text
    list_data = list_resp.json()

    assert list_data["total"] == 2
    assert len(list_data["items"]) == 2
    assert list_data["items"][0]["offer_id"] in {"OFFER-001", "OFFER-002"}
    assert list_data["items"][0]["name"] in {"Test Product 1", "Test Product 2"}


@pytest.mark.asyncio
async def test_kaspi_products_sync_idempotent(
    monkeypatch,
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    """
    Test that repeated sync is idempotent (no duplicate records).

    Scenario:
    1. First sync: inserted=2
    2. Second sync: inserted=0, updated=2 (same data)
    3. Verify: still only 2 records in DB
    """
    from app.services.kaspi_service import KaspiService

    # Mock get_products
    async def fake_get_products(self, *, page=1, page_size=100):  # noqa: ARG001
        return _fake_products_payload()

    monkeypatch.setattr(KaspiService, "get_products", fake_get_products)

    # First sync
    resp1 = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["inserted"] == 2
    assert data1["updated"] == 0

    # Second sync
    resp2 = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["inserted"] == 0
    assert data2["updated"] == 2  # Same products updated

    # Verify no duplicates
    res = await async_db_session.execute(
        sa.select(sa.func.count(KaspiCatalogProduct.id)).where(KaspiCatalogProduct.company_id == 1001)
    )
    count = res.scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_kaspi_products_tenant_isolation(
    monkeypatch,
    async_client,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    """
    Test tenant isolation: company A products not visible to company B.

    Scenario:
    1. Sync products for company A (2 products)
    2. Sync products for company B (different 2 products)
    3. List products for company A: should see only A's products
    4. List products for company B: should see only B's products
    """
    from app.services.kaspi_service import KaspiService

    # Mock get_products to return different data based on call count
    call_count = [0]

    async def fake_get_products(self, *, page=1, page_size=100):  # noqa: ARG001
        call_count[0] += 1
        if call_count[0] == 1:
            # Company A products
            return [
                {
                    "id": "kaspi-a-001",
                    "offer_id": "OFFER-A-001",
                    "name": "Company A Product 1",
                    "sku": "SKU-A-001",
                    "price": 5000,
                    "qty": 10,
                },
                {
                    "id": "kaspi-a-002",
                    "offer_id": "OFFER-A-002",
                    "name": "Company A Product 2",
                    "sku": "SKU-A-002",
                    "price": 6000,
                    "qty": 20,
                },
            ]
        else:
            # Company B products
            return [
                {
                    "id": "kaspi-b-001",
                    "offer_id": "OFFER-B-001",
                    "name": "Company B Product 1",
                    "sku": "SKU-B-001",
                    "price": 7000,
                    "qty": 30,
                },
                {
                    "id": "kaspi-b-002",
                    "offer_id": "OFFER-B-002",
                    "name": "Company B Product 2",
                    "sku": "SKU-B-002",
                    "price": 8000,
                    "qty": 40,
                },
            ]

    monkeypatch.setattr(KaspiService, "get_products", fake_get_products)

    # Sync for company A
    resp_a = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp_a.status_code == 200
    assert resp_a.json()["inserted"] == 2

    # Sync for company B
    resp_b = await async_client.post("/api/v1/kaspi/products/sync", headers=company_b_admin_headers)
    assert resp_b.status_code == 200
    assert resp_b.json()["inserted"] == 2

    # List for company A
    list_a = await async_client.get("/api/v1/kaspi/products", headers=company_a_admin_headers)
    assert list_a.status_code == 200
    data_a = list_a.json()
    assert data_a["total"] == 2
    offer_ids_a = {item["offer_id"] for item in data_a["items"]}
    assert offer_ids_a == {"OFFER-A-001", "OFFER-A-002"}

    # List for company B
    list_b = await async_client.get("/api/v1/kaspi/products", headers=company_b_admin_headers)
    assert list_b.status_code == 200
    data_b = list_b.json()
    assert data_b["total"] == 2
    offer_ids_b = {item["offer_id"] for item in data_b["items"]}
    assert offer_ids_b == {"OFFER-B-001", "OFFER-B-002"}

    # Verify no cross-contamination
    assert offer_ids_a.isdisjoint(offer_ids_b)
