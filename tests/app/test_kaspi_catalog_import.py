import pytest
from sqlalchemy import select

from app.models.catalog_import import CatalogImportBatch, CatalogImportRow
from app.models.kaspi_offer import KaspiOffer


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _json_bytes(text: str) -> bytes:
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
    assert data["rows_skipped"] == 0
    assert data["top_errors"] == []

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
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S1", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .all()
    )
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
    assert data["rows_skipped"] == 1
    assert data["top_errors"][0]["error"] == "missing_sku"
    assert data["top_errors"][0]["count"] == 1


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
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S2", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .first()
    )
    assert offer is not None
    assert float(offer.price or 0) == 12.0
    assert offer.stock_count == 7


@pytest.mark.asyncio
async def test_kaspi_catalog_import_ux_rbac(async_client, company_a_admin_headers, company_a_manager_headers):
    csv_data = "SKU,Title,Price\nS1,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    batch_id = resp.json()["batch_id"]

    resp = await async_client.get(
        "/api/v1/kaspi/catalog/import/batches",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 403

    resp = await async_client.get(
        f"/api/v1/kaspi/catalog/import/batches/{batch_id}",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 403

    resp = await async_client.get(
        f"/api/v1/kaspi/catalog/import/batches/{batch_id}/errors",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 403

    resp = await async_client.get(
        "/api/v1/kaspi/offers",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_kaspi_catalog_import_ux_tenant_isolation(
    async_client,
    company_a_admin_headers,
    company_b_admin_headers,
):
    csv_data = "SKU,Title,Price\nS1,Item 1,1000\n"
    resp_a = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "MA"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp_a.status_code == 200
    batch_a = resp_a.json()["batch_id"]

    resp_b = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_b_admin_headers,
        params={"merchantUid": "MB"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp_b.status_code == 200
    batch_b = resp_b.json()["batch_id"]

    list_a = await async_client.get(
        "/api/v1/kaspi/catalog/import/batches",
        headers=company_a_admin_headers,
    )
    assert list_a.status_code == 200
    batch_ids_a = {item["id"] for item in list_a.json()}
    assert batch_b not in batch_ids_a
    assert batch_a in batch_ids_a

    detail_b = await async_client.get(
        f"/api/v1/kaspi/catalog/import/batches/{batch_b}",
        headers=company_a_admin_headers,
    )
    assert detail_b.status_code == 404

    errors_b = await async_client.get(
        f"/api/v1/kaspi/catalog/import/batches/{batch_b}/errors",
        headers=company_a_admin_headers,
    )
    assert errors_b.status_code == 404

    offers_a = await async_client.get(
        "/api/v1/kaspi/offers",
        headers=company_a_admin_headers,
        params={"merchantUid": "MB"},
    )
    assert offers_a.status_code == 200
    assert offers_a.json()["total"] == 0


@pytest.mark.asyncio
async def test_kaspi_catalog_import_errors_endpoint(async_client, company_a_admin_headers):
    csv_data = "SKU,Title,Price\n,Missing SKU,1000\nS1,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    batch_id = resp.json()["batch_id"]

    errors_resp = await async_client.get(
        f"/api/v1/kaspi/catalog/import/batches/{batch_id}/errors",
        headers=company_a_admin_headers,
    )
    assert errors_resp.status_code == 200
    errors = errors_resp.json()
    assert len(errors) == 1
    assert errors[0]["error"] == "missing_sku"
    assert errors[0]["row_num"] == 1


@pytest.mark.asyncio
async def test_kaspi_offers_list_pagination_and_filters(async_client, company_a_admin_headers):
    csv_data = "SKU,Title,Price\n" "S1,Alpha Item,1000\n" "S2,Beta Item,1100\n" "S3,Alpha Extra,1200\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200

    list_resp = await async_client.get(
        "/api/v1/kaspi/offers",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1", "limit": 1, "offset": 1},
    )
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 1

    filter_resp = await async_client.get(
        "/api/v1/kaspi/offers",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1", "q": "Alpha"},
    )
    assert filter_resp.status_code == 200
    filter_data = filter_resp.json()
    assert filter_data["total"] == 2


@pytest.mark.asyncio
async def test_kaspi_catalog_import_master_sku_not_overwritten(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    csv_data = (
        "sku,master_sku,merchant_uid,title,price,old_price,stock_count,pre_order,stock_specified\n"
        "S10,MS10,MERCH-CSV,Item 10,1000,1100,5,true,false\n"
    )
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_ok"] == 1
    assert data["rows_skipped"] == 0

    await async_db_session.rollback()
    offer = (
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S10", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .first()
    )
    assert offer is not None
    assert offer.master_sku == "MS10"
    assert offer.merchant_uid == "M1"
    assert offer.master_sku != offer.merchant_uid


@pytest.mark.asyncio
async def test_kaspi_catalog_import_json_data_array_ok(async_client, async_db_session, company_a_admin_headers):
    json_data = (
        "{\n"
        '  "data": [\n'
        '    {"offerId": "", "sku": "S10", "masterSku": "MS10", '
        '"name": "Item 10", "price": "1000"}\n'
        "  ]\n"
        "}\n"
    )
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.json", _json_bytes(json_data), "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_ok"] == 1

    await async_db_session.rollback()
    offer = (
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S10", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .first()
    )
    assert offer is not None
    assert offer.sku == "S10"
    assert offer.master_sku == "MS10"
    assert offer.raw.get("sku") == "S10"


@pytest.mark.asyncio
async def test_kaspi_catalog_import_jsonl_ok(async_client, async_db_session, company_a_admin_headers):
    jsonl_data = '{"sku": "S11", "masterSku": "MS11", "name": "Item 11", "price": "1000"}\n'
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.jsonl", _json_bytes(jsonl_data), "application/x-ndjson")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_ok"] == 1

    await async_db_session.rollback()
    offer = (
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S11", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .first()
    )
    assert offer is not None
    assert offer.sku == "S11"
    assert offer.master_sku == "MS11"


@pytest.mark.asyncio
async def test_kaspi_catalog_import_alias_collision_prefers_primary(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    csv_data = "SKU,Offer_ID,Title,Price\nS1,S2,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_ok"] == 1

    await async_db_session.rollback()
    offer = (
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S1", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .first()
    )
    assert offer is not None
    assert offer.sku == "S1"


@pytest.mark.asyncio
async def test_kaspi_catalog_import_deduplicates_by_sku(async_client, async_db_session, company_a_admin_headers):
    csv_data = "SKU,Title,Price,Stock\nS1,Item 1,1000,5\nS1,Item 1,1200,7\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200

    await async_db_session.rollback()
    offers = (
        (
            await async_db_session.execute(
                select(KaspiOffer).where(KaspiOffer.sku == "S1", KaspiOffer.merchant_uid == "M1")
            )
        )
        .scalars()
        .all()
    )
    assert len(offers) == 1
    assert float(offers[0].price or 0) == 1200.0


@pytest.mark.asyncio
async def test_kaspi_catalog_import_dry_run(async_client, async_db_session, company_a_admin_headers):
    csv_data = "SKU,Title,Price\nS1,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1", "dry_run": "true"},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is True
    assert data["status"] == "DRY_RUN"
    assert data["rows_ok"] == 1

    await async_db_session.rollback()
    batch = (await async_db_session.execute(select(CatalogImportBatch))).scalars().first()
    row = (await async_db_session.execute(select(CatalogImportRow))).scalars().first()
    offer = (await async_db_session.execute(select(KaspiOffer))).scalars().first()
    assert batch is None
    assert row is None
    assert offer is None
