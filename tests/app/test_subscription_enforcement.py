import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
@pytest.mark.no_subscription
async def test_subscription_required_blocks_protected_endpoint(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    resp = await async_client.get("/api/v1/products", headers=company_a_admin_headers)
    assert resp.status_code == 402
    assert resp.json().get("detail") == "subscription_required"


@pytest.mark.asyncio
async def test_subscription_allows_access(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/subscriptions",
        headers=company_a_admin_headers,
        json={
            "plan": "Start",
            "billing_cycle": "monthly",
            "price": "0.00",
            "currency": "KZT",
            "trial_days": 0,
        },
    )
    assert created.status_code == 201, created.text

    resp = await async_client.get("/api/v1/products", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_cross_tenant_subscription_does_not_grant_access(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await async_client.post(
        "/api/v1/subscriptions",
        headers=company_a_admin_headers,
        json={
            "plan": "Start",
            "billing_cycle": "monthly",
            "price": "0.00",
            "currency": "KZT",
            "trial_days": 0,
        },
    )
    assert created.status_code == 201, created.text

    foreign = await async_client.get("/api/v1/products", headers=company_b_admin_headers)
    assert foreign.status_code == 402
    assert foreign.json().get("detail") == "subscription_required"
