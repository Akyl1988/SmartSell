from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_kaspi_employee_forbidden_before_feature(async_client, company_a_employee_headers):
    resp = await async_client.get("/api/v1/kaspi/orders", headers=company_a_employee_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")
