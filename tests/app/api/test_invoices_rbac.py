from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_invoices_employee_forbidden(async_client, company_a_employee_headers):
    resp = await async_client.get("/api/v1/invoices", headers=company_a_employee_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")


async def test_invoices_admin_allowed(async_client, company_a_admin_headers):
    resp = await async_client.get("/api/v1/invoices", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text


async def test_invoices_platform_admin_allowed(async_client, auth_headers):
    resp = await async_client.get("/api/v1/invoices", headers=auth_headers)
    assert resp.status_code == 200, resp.text
