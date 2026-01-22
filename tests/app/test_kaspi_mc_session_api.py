import pytest

from app.api.v1 import kaspi as kaspi_api
from app.models.company import Company
from app.models.kaspi_mc_session import KaspiMcSession


async def _ensure_company(async_db_session, company_id: int) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.commit()


@pytest.mark.asyncio
async def test_mc_session_create_and_get(async_client, async_db_session, company_a_admin_headers):
    await _ensure_company(async_db_session, 1001)

    resp = await async_client.post(
        "/api/v1/kaspi/mc/session",
        params={"merchantUid": "17319385"},
        headers=company_a_admin_headers,
        json={"cookie": "a=b; c=d", "x_auth_version": 3, "comment": "test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["merchant_uid"] == "17319385"
    assert data["x_auth_version"] == 3
    assert data["comment"] == "test"
    assert "cookie" not in data
    assert data.get("cookies_masked") is not None
    assert data["cookies_masked"].endswith("...")

    resp2 = await async_client.get(
        "/api/v1/kaspi/mc/session",
        params={"merchantUid": "17319385"},
        headers=company_a_admin_headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["merchant_uid"] == "17319385"
    assert data2["x_auth_version"] == 3
    assert data2["comment"] == "test"


@pytest.mark.asyncio
async def test_mc_session_create_forbidden(async_client, company_a_manager_headers):
    resp = await async_client.post(
        "/api/v1/kaspi/mc/session",
        params={"merchantUid": "17319385"},
        headers=company_a_manager_headers,
        json={"cookie": "a=b; c=d"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mc_sync_uses_session(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    await _ensure_company(async_db_session, 1001)

    async_db_session.add(
        KaspiMcSession(
            company_id=1001,
            merchant_uid="17319385",
            cookies_ciphertext=b"cookie",
            is_active=True,
            x_auth_version=3,
        )
    )
    await async_db_session.commit()

    async def _fake_get_cookies(*args, **kwargs):
        return "a=b; c=d"

    called = {}

    async def _fake_sync(session, *, company_id, merchant_uid, cookies, x_auth_version, page_limit, max_pages):
        called.update(
            {
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "cookies": cookies,
                "x_auth_version": x_auth_version,
                "page_limit": page_limit,
                "max_pages": max_pages,
            }
        )
        return {"rows_total": 2, "rows_ok": 2, "rows_failed": 0, "errors": []}

    monkeypatch.setattr(KaspiMcSession, "get_cookies", _fake_get_cookies)
    monkeypatch.setattr(kaspi_api, "sync_kaspi_mc_offers", _fake_sync)

    resp = await async_client.post(
        "/api/v1/kaspi/mc/sync",
        params={"merchantUid": "17319385", "limit": 50, "max_pages": 10},
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DONE"
    assert data["merchant_uid"] == "17319385"
    assert called["x_auth_version"] == 3
    assert called["page_limit"] == 50
    assert called["max_pages"] == 10
