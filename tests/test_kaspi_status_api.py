from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_feed_export import KaspiFeedExport
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.marketplace import KaspiStoreToken


@pytest.mark.asyncio
async def test_kaspi_status_returns_latest_feed_and_counts(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    """Status endpoint returns latest feed, catalog aggregates, orders sync, and health for tenant."""

    company_id = 1001
    store_name = "store-status-a"

    # Clean previous data for this company
    await async_db_session.execute(sa.delete(KaspiFeedExport).where(KaspiFeedExport.company_id == company_id))
    await async_db_session.execute(sa.delete(KaspiCatalogProduct).where(KaspiCatalogProduct.company_id == company_id))
    await async_db_session.execute(sa.delete(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id))
    await async_db_session.execute(sa.delete(KaspiStoreToken).where(KaspiStoreToken.store_name == store_name))

    # Configure company
    company = (await async_db_session.execute(sa.select(Company).where(Company.id == company_id))).scalars().first()
    company.kaspi_store_id = store_name

    # Token (masked/boolean only)
    async_db_session.add(KaspiStoreToken(store_name=store_name, token_ciphertext=b"secret"))

    # Feed export (latest)
    now = datetime.utcnow()
    long_error = "E" * 600
    feed = KaspiFeedExport(
        company_id=company_id,
        kind="products",
        format="xml",
        status="uploaded",
        checksum="status-feed-chk-1",
        payload_text="<products></products>",
        attempts=2,
        last_attempt_at=now - timedelta(minutes=5),
        uploaded_at=now,
        duration_ms=123,
        last_error=long_error,
        created_at=now - timedelta(minutes=10),
    )
    async_db_session.add(feed)

    # Catalog products
    prod1 = KaspiCatalogProduct(
        company_id=company_id,
        offer_id="OFFER-STAT-1",
        name="Status Product 1",
        sku="SKU-STAT-1",
        price=100,
        qty=5,
        is_active=True,
        updated_at=now,
    )
    prod2 = KaspiCatalogProduct(
        company_id=company_id,
        offer_id="OFFER-STAT-2",
        name="Status Product 2",
        sku="SKU-STAT-2",
        price=50,
        qty=0,
        is_active=False,
        updated_at=now - timedelta(minutes=1),
    )
    async_db_session.add_all([prod1, prod2])

    # Orders sync state
    sync_state = KaspiOrderSyncState(
        company_id=company_id,
        last_synced_at=now - timedelta(hours=1),
        last_external_order_id="ord-123",
        last_attempt_at=now - timedelta(minutes=2),
        last_duration_ms=456,
        last_result="ok",
        last_fetched=3,
        last_inserted=2,
        last_updated=1,
        last_error_at=None,
        last_error_code=None,
        last_error_message=None,
        updated_at=now - timedelta(minutes=2),
    )
    async_db_session.add(sync_state)

    await async_db_session.commit()

    resp = await async_client.get("/api/v1/kaspi/status", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    feed_data = data["feeds"]["products_latest"]
    assert feed_data["id"] == feed.id
    assert feed_data["attempts"] == 2
    assert feed_data["status"] == "uploaded"
    assert len(feed_data["last_error"]) <= 500
    assert feed_data["last_error"].startswith("E")

    catalog = data["catalog"]
    assert catalog["total"] == 2
    assert catalog["active"] == 1
    assert catalog["last_updated_at"] is not None

    orders_sync = data["orders_sync"]
    assert orders_sync["last_result"] == "ok"
    assert orders_sync["last_fetched"] == 3

    health = data["health"]
    assert health["has_kaspi_token_configured"] is True


@pytest.mark.asyncio
async def test_kaspi_status_is_tenant_isolated_and_null_when_no_feed(
    async_client,
    async_db_session,
    company_b_admin_headers,
):
    """Tenant B should not see company A data and gets nulls when no records exist."""

    company_id_b = 2001

    # Cleanup B data
    await async_db_session.execute(sa.delete(KaspiFeedExport).where(KaspiFeedExport.company_id == company_id_b))
    await async_db_session.execute(sa.delete(KaspiCatalogProduct).where(KaspiCatalogProduct.company_id == company_id_b))
    await async_db_session.execute(sa.delete(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id_b))

    company_b = (await async_db_session.execute(sa.select(Company).where(Company.id == company_id_b))).scalars().first()
    company_b.kaspi_store_id = None

    await async_db_session.commit()

    resp = await async_client.get("/api/v1/kaspi/status", headers=company_b_admin_headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["feeds"]["products_latest"] is None
    assert data["catalog"]["total"] == 0
    assert data["catalog"]["active"] == 0
    assert data["catalog"]["last_updated_at"] is None
    assert data["orders_sync"] is None
    assert data["health"]["has_kaspi_token_configured"] is False
