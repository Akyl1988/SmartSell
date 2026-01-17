import pytest
from sqlalchemy import select

from app.models.catalog_import import CatalogImportRow
from app.models.kaspi_offer import KaspiOffer


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


@pytest.mark.asyncio
async def test_kaspi_catalog_import_rbac(async_client, company_a_manager_headers):
    csv_data = "SKU,Title,Price\nS1,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_manager_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_kaspi_catalog_import_missing_merchant_uid(async_client, company_a_admin_headers):
    csv_data = "SKU,Title,Price\nS1,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing_merchant_uid"


@pytest.mark.asyncio
async def test_kaspi_catalog_import_header_aliases(async_client, async_db_session, company_a_admin_headers):
    csv_data = "Master SKU,SKU,Title,Price,Stock,Pre Order\nMS1,S1,Item 1,1000,5,yes\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_ok"] == 1
    assert data["rows_failed"] == 0

    await async_db_session.rollback()
    row = (await async_db_session.execute(select(CatalogImportRow))).scalars().first()
    assert row is not None
    assert row.sku == "S1"
    assert row.master_sku == "MS1"

    offer = (await async_db_session.execute(select(KaspiOffer))).scalars().first()
    assert offer is not None
    assert offer.sku == "S1"
    assert offer.merchant_uid == "M1"
    assert float(offer.price or 0) == 1000.0
    assert offer.stock_count == 5


@pytest.mark.asyncio
async def test_kaspi_catalog_import_idempotent(async_client, async_db_session, company_a_admin_headers):
    csv_first = "SKU,Title,Price,Stock\nS1,Item 1,1000,5\n"
    resp1 = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_first), "text/csv")},
    )
    assert resp1.status_code == 200

    csv_second = "SKU,Title,Price,Stock\nS1,Item 1,1200,7\n"
    resp2 = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_second), "text/csv")},
    )
    assert resp2.status_code == 200

    await async_db_session.rollback()
    offers = (
        await async_db_session.execute(
            select(KaspiOffer).where(KaspiOffer.sku == "S1", KaspiOffer.merchant_uid == "M1")
        )
    ).scalars().all()
    assert len(offers) == 1
    offer = offers[0]
    assert float(offer.price or 0) == 1200.0
    assert offer.stock_count == 7


@pytest.mark.asyncio
async def test_kaspi_catalog_import_missing_sku(async_client, company_a_admin_headers):
    csv_data = "SKU,Title,Price\n,NoSku,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_failed"] == 1
    assert data["errors"][0]["error"] == "missing_sku"


@pytest.mark.asyncio
async def test_kaspi_catalog_import_numeric_parsing(async_client, async_db_session, company_a_admin_headers):
    csv_data = 'SKU,Title,Price,Stock\nS2,Item 2,"12,7",7\n'
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200

    await async_db_session.rollback()
    offer = (
        await async_db_session.execute(
            select(KaspiOffer).where(KaspiOffer.sku == "S2", KaspiOffer.merchant_uid == "M1")
        )
    ).scalars().first()
    assert offer is not None
    assert float(offer.price or 0) == 12.0
    assert offer.stock_count == 7
