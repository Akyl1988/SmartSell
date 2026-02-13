from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_payments_employee_forbidden(async_client, company_a_employee_headers):
    resp = await async_client.get("/api/v1/payments/", headers=company_a_employee_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") in {"ADMIN_REQUIRED", "FORBIDDEN", "HTTP_403"}
    assert payload.get("request_id")


async def test_payments_admin_allowed(async_client, company_a_admin_headers):
    resp = await async_client.get("/api/v1/payments/", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text


async def test_payments_manager_subscription_gated(async_client, company_a_manager_headers):
    payload = {
        "amount": "10.00",
        "currency": "KZT",
        "customer_id": "cust-1",
        "metadata": {"source": "rbac"},
    }
    resp = await async_client.post("/api/v1/payments/intents", json=payload, headers=company_a_manager_headers)
    assert resp.status_code not in (401, 403), resp.text
