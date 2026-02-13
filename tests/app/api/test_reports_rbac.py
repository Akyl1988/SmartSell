from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_reports_employee_forbidden(async_client, company_a_employee_headers):
    resp = await async_client.get("/api/v1/reports/orders.pdf", headers=company_a_employee_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")
