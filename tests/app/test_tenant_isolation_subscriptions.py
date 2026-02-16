import pytest

BASE = "/api/v1/subscriptions"


@pytest.mark.asyncio
async def test_subscriptions_list_isolated_between_companies(
    async_client, company_a_admin_headers, company_b_admin_headers
):
    payload = {
        "plan": "basic",
        "billing_cycle": "monthly",
        "price": "10",
        "currency": "KZT",
    }
    created = await async_client.post(BASE, json=payload, headers=company_a_admin_headers)
    assert created.status_code == 201, created.text

    forbidden = await async_client.get(BASE, params={"company_id": 1001}, headers=company_b_admin_headers)
    assert forbidden.status_code == 200, forbidden.text
    assert forbidden.json() == []


@pytest.mark.asyncio
async def test_subscription_payments_hidden_from_other_company(
    async_client, company_a_admin_headers, company_b_admin_headers
):
    payload = {
        "company_id": 1001,
        "plan": "pro",
        "billing_cycle": "monthly",
        "price": "25",
        "currency": "KZT",
    }
    created = await async_client.post(BASE, json=payload, headers=company_a_admin_headers)
    assert created.status_code == 201, created.text
    subscription_id = created.json()["id"]

    payments_resp = await async_client.get(f"{BASE}/{subscription_id}/payments", headers=company_b_admin_headers)
    assert payments_resp.status_code == 404, payments_resp.text
