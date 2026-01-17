import pytest
from sqlalchemy import select

from app.models.kaspi_feed_export import KaspiFeedExport


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


async def _create_offers(async_client, headers, merchant_uid: str = "M1") -> None:
    csv_data = "SKU,Title,Price\nS1,Item 1,1000\n"
    resp = await async_client.post(
        "/api/v1/kaspi/catalog/import",
        headers=headers,
        params={"merchantUid": merchant_uid},
        files={"file": ("catalog.csv", _csv_bytes(csv_data), "text/csv")},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_kaspi_feed_export_missing_merchant_uid(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/kaspi/feed/exports",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing_merchant_uid"


@pytest.mark.asyncio
async def test_kaspi_feed_export_rbac(async_client, company_a_manager_headers):
    resp = await async_client.post(
        "/api/v1/kaspi/feed/exports",
        headers=company_a_manager_headers,
        params={"merchantUid": "M1"},
    )
    assert resp.status_code == 403

    resp = await async_client.get(
        "/api/v1/kaspi/feed/exports",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_kaspi_feed_export_tenant_isolation(
    async_client,
    company_a_admin_headers,
    company_b_admin_headers,
):
    await _create_offers(async_client, company_a_admin_headers, merchant_uid="MA")
    await _create_offers(async_client, company_b_admin_headers, merchant_uid="MB")

    export_b = await async_client.post(
        "/api/v1/kaspi/feed/exports",
        headers=company_b_admin_headers,
        params={"merchantUid": "MB"},
    )
    assert export_b.status_code == 200
    export_b_id = export_b.json()["id"]

    list_a = await async_client.get(
        "/api/v1/kaspi/feed/exports",
        headers=company_a_admin_headers,
    )
    assert list_a.status_code == 200
    ids_a = {item["id"] for item in list_a.json()}
    assert export_b_id not in ids_a

    detail_b = await async_client.get(
        f"/api/v1/kaspi/feed/exports/{export_b_id}",
        headers=company_a_admin_headers,
    )
    assert detail_b.status_code == 404


@pytest.mark.asyncio
async def test_kaspi_feed_export_happy_path(async_client, company_a_admin_headers):
    await _create_offers(async_client, company_a_admin_headers, merchant_uid="M1")
    create_resp = await async_client.post(
        "/api/v1/kaspi/feed/exports",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
    )
    assert create_resp.status_code == 200
    export_id = create_resp.json()["id"]

    download_resp = await async_client.get(
        f"/api/v1/kaspi/feed/exports/{export_id}/download",
        headers=company_a_admin_headers,
    )
    assert download_resp.status_code == 200
    assert download_resp.headers.get("content-type", "").startswith("application/xml")
    body = download_resp.text
    assert "S1" in body
    assert "Item 1" in body
    assert "1000.00" in body


@pytest.mark.asyncio
async def test_kaspi_feed_export_download_requires_done(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    export = KaspiFeedExport(
        company_id=1001,
        kind="offers",
        format="xml",
        status="RUNNING",
        checksum="0" * 64,
        payload_text="",
    )
    async_db_session.add(export)
    await async_db_session.commit()
    await async_db_session.refresh(export)

    resp = await async_client.get(
        f"/api/v1/kaspi/feed/exports/{export.id}/download",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 409
