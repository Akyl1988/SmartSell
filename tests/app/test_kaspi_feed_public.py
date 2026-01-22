import xml.etree.ElementTree as ET

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


async def _create_offer_with_city_prices(async_db_session, company_id: int, merchant_uid: str, sku: str) -> None:
    async_db_session.add(
        KaspiOffer(
            company_id=company_id,
            merchant_uid=merchant_uid,
            sku=sku,
            title="City Item",
            price=1500,
            raw={"cityPrices": [{"cityId": 750000000, "value": 1200, "oldprice": 1500}]},
        )
    )
    await async_db_session.commit()


def _find_child(root: ET.Element, tag: str) -> ET.Element | None:
    return root.find(f"{{kaspiShopping}}{tag}")


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
async def test_public_feed_schema_minimal(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _create_company(async_db_session, 1001)
    await _create_offer_with_city_prices(async_db_session, 1001, "M1", "S-CITY")

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

    root = ET.fromstring(resp.text)
    assert root.tag == "{kaspiShopping}kaspi_catalog"
    assert root.attrib.get("date")

    company_el = _find_child(root, "company")
    merchant_el = _find_child(root, "merchantid")
    offers_el = _find_child(root, "offers")
    assert company_el is not None
    assert merchant_el is not None
    assert offers_el is not None

    offer_el = offers_el.find("{kaspiShopping}offer")
    assert offer_el is not None
    assert offer_el.attrib.get("sku")

    model_el = offer_el.find("{kaspiShopping}model")
    assert model_el is not None and (model_el.text or "").strip()

    price_el = offer_el.find("{kaspiShopping}price")
    cityprices_el = offer_el.find("{kaspiShopping}cityprices")
    assert (price_el is not None) or (cityprices_el is not None)


@pytest.mark.asyncio
async def test_public_feed_token_returns_in_default_dev_env(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    await _create_company(async_db_session, 1001)

    token_resp = await async_client.post(
        "/api/v1/kaspi/feed/public-tokens",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"comment": "test"},
    )
    assert token_resp.status_code == 200
    assert token_resp.json().get("token")


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
