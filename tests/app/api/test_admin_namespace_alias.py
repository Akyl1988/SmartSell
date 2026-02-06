from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_legacy_admin_integrations_platform_only(async_client, auth_headers, company_a_admin_headers):
    resp_store_admin = await async_client.get(
        "/api/admin/integrations/providers",
        headers=company_a_admin_headers,
    )
    assert resp_store_admin.status_code == 403, resp_store_admin.text
    payload = resp_store_admin.json()
    assert payload.get("code") == "ADMIN_REQUIRED"

    resp_platform_admin = await async_client.get(
        "/api/admin/integrations/providers",
        headers=auth_headers,
    )
    assert resp_platform_admin.status_code == 200, resp_platform_admin.text


async def test_v1_admin_integrations_platform_only(async_client, auth_headers, company_a_admin_headers):
    resp_store_admin = await async_client.get(
        "/api/v1/admin/integrations/providers",
        headers=company_a_admin_headers,
    )
    assert resp_store_admin.status_code == 403, resp_store_admin.text
    payload = resp_store_admin.json()
    assert payload.get("code") == "ADMIN_REQUIRED"

    resp_platform_admin = await async_client.get(
        "/api/v1/admin/integrations/providers",
        headers=auth_headers,
    )
    assert resp_platform_admin.status_code == 200, resp_platform_admin.text
