import pytest


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


async def _create_export(async_client, headers, merchant_uid: str = "M1") -> int:
    resp = await async_client.post(
        "/api/v1/kaspi/feed/exports",
        headers=headers,
        params={"merchantUid": merchant_uid},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_kaspi_feed_public_token_create(async_client, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"]
    assert data["merchant_uid"] == "M1"
    assert data["token"]


@pytest.mark.asyncio
async def test_kaspi_feed_public_missing_token(async_client):
    resp = await async_client.get("/api/v1/kaspi/feed/public/offers.xml", params={"merchantUid": "M1"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_kaspi_feed_public_invalid_token(async_client):
    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "M1", "token": "invalid"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_kaspi_feed_public_wrong_merchant_uid(async_client, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token = resp.json()["token"]

    bad = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "M2", "token": token},
    )
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_kaspi_feed_public_tenant_isolation(
    async_client,
    company_a_admin_headers,
    company_b_admin_headers,
    monkeypatch,
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_offers(async_client, company_a_admin_headers, merchant_uid="MA")
    await _create_offers(async_client, company_b_admin_headers, merchant_uid="MB")
    await _create_export(async_client, company_b_admin_headers, merchant_uid="MB")

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "MA"},
        json={"comment": "test"},
    )
    token = token_resp.json()["token"]

    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "MB", "token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_kaspi_feed_public_happy_path(async_client, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_offers(async_client, company_a_admin_headers, merchant_uid="M1")
    await _create_export(async_client, company_a_admin_headers, merchant_uid="M1")

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token = token_resp.json()["token"]

    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "M1", "token": token},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/xml")
    assert "S1" in resp.text
    assert "Item 1" in resp.text
    assert "1000.00" in resp.text
