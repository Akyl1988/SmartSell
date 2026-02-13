from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_analytics_employee_forbidden(async_client, company_a_employee_headers):
    resp = await async_client.get("/api/v1/analytics/dashboard", headers=company_a_employee_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "FORBIDDEN"
    assert payload.get("request_id")


async def test_analytics_admin_allowed(async_client, company_a_admin_headers):
    resp = await async_client.get("/api/v1/analytics/dashboard", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text


async def test_analytics_platform_admin_forbidden(async_client, auth_headers):
    resp = await async_client.get("/api/v1/analytics/dashboard", headers=auth_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "FORBIDDEN"
    assert payload.get("request_id")
