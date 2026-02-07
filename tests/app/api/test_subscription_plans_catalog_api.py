import pytest

BASE = "/api/v1/subscriptions/plans"


@pytest.mark.asyncio
async def test_subscription_plans_catalog_admin_ok(client, auth_headers):
    resp = await client.get(BASE, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert [item["plan_id"] for item in data] == ["start", "business", "pro"]
    assert [item["plan"] for item in data] == ["Start", "Business", "Pro"]


@pytest.mark.asyncio
async def test_subscription_plans_catalog_non_admin_allowed(client, company_a_admin_headers):
    resp = await client.get(BASE, headers=company_a_admin_headers)
    assert resp.status_code == 200
