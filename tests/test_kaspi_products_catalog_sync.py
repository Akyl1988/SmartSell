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

from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.marketplace import KaspiStoreToken


async def _ensure_company_store(async_db_session, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}", kaspi_store_id=store_id)
        async_db_session.add(company)
    else:
        company.kaspi_store_id = store_id
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_kaspi_products_sync_creates_and_lists(
    monkeypatch,
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    """
    Test that sync endpoint is not supported for catalog pull.
    """
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a" if store_name == "store-a" else None

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    resp = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data["code"] == "catalog_pull_not_supported"

    # Verify in database
    res = await async_db_session.execute(
        sa.select(sa.func.count(KaspiCatalogProduct.id)).where(KaspiCatalogProduct.company_id == 1001)
    )
    count = res.scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_kaspi_products_sync_idempotent(
    monkeypatch,
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    """
    Test that repeated sync calls return not supported.
    """
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a" if store_name == "store-a" else None

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    # First sync
    resp1 = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp1.status_code == 409

    # Second sync
    resp2 = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp2.status_code == 409

    # Verify no duplicates
    res = await async_db_session.execute(
        sa.select(sa.func.count(KaspiCatalogProduct.id)).where(KaspiCatalogProduct.company_id == 1001)
    )
    count = res.scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_kaspi_products_tenant_isolation(
    monkeypatch,
    async_client,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    """
    Test tenant isolation: sync endpoint remains not supported for both companies.
    """
    await _ensure_company_store(async_db_session, 1001, "store-a")
    await _ensure_company_store(async_db_session, 2001, "store-b")

    async def _get_token(session, store_name: str):
        return "token-a" if store_name == "store-a" else "token-b"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    # Sync for company A
    resp_a = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp_a.status_code == 409

    # Sync for company B
    resp_b = await async_client.post("/api/v1/kaspi/products/sync", headers=company_b_admin_headers)
    assert resp_b.status_code == 409
