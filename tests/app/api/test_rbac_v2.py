import pytest


@pytest.mark.asyncio
async def test_wallet_requires_token(async_client):
    resp = await async_client.get("/api/v1/wallet/accounts")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wallet_forbids_insufficient_role(async_client, company_a_storekeeper_headers):
    # Storekeeper role cannot create wallet accounts (admin/manager only)
    payload = {"user_id": 1, "currency": "KZT"}

    resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json=payload,
        headers=company_a_storekeeper_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_wallet_forbids_cross_company_query(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/wallet/accounts",
        params={"company_id": 2001},
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_wallet_allows_company_admin(async_client, company_a_admin_headers):
    resp = await async_client.get("/api/v1/wallet/accounts", headers=company_a_admin_headers)
    assert resp.status_code == 200
