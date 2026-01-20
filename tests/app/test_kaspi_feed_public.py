import pytest

from app.models.company import Company
from app.models.kaspi_offer import KaspiOffer


async def _create_company(async_db_session, company_id: int) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.commit()


async def _create_offer(async_db_session, company_id: int, merchant_uid: str, sku: str) -> None:
    async_db_session.add(
        KaspiOffer(
            company_id=company_id,
            merchant_uid=merchant_uid,
            sku=sku,
            title="Item 1",
            price=1000,
        )
    )
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_public_feed_ok(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_company(async_db_session, 1001)
    await _create_offer(async_db_session, 1001, "M1", "S1")

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token = token_resp.json()["token"]

    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"token": token},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/xml")
    assert "S1" in resp.text


@pytest.mark.asyncio
async def test_public_feed_not_found(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_company(async_db_session, 1001)

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token = token_resp.json()["token"]

    missing_token = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "M1"},
    )
    assert missing_token.status_code == 404

    invalid = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "M1", "token": "invalid"},
    )
    assert invalid.status_code == 404

    no_offers = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"token": token},
    )
    assert no_offers.status_code == 404


@pytest.mark.asyncio
async def test_public_feed_revoked_token(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_company(async_db_session, 1001)
    await _create_offer(async_db_session, 1001, "M1", "S1")

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token_id = token_resp.json()["id"]
    token = token_resp.json()["token"]

    revoke_resp = await async_client.post(
        f"/api/v1/kaspi/feed/public-tokens/{token_id}/revoke",
        headers=company_a_admin_headers,
    )
    assert revoke_resp.status_code == 200

    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_feed_wrong_merchant_uid(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_company(async_db_session, 1001)
    await _create_offer(async_db_session, 1001, "M1", "S1")

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token = token_resp.json()["token"]

    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"merchantUid": "M2", "token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_feed_tenant_isolation(
    async_client,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
    monkeypatch,
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_company(async_db_session, 1001)
    await _create_company(async_db_session, 2001)
    await _create_offer(async_db_session, 2001, "M1", "S1")

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    token = token_resp.json()["token"]

    resp = await async_client.get(
        "/api/v1/kaspi/feed/public/offers.xml",
        params={"token": token},
    )
    assert resp.status_code == 404
