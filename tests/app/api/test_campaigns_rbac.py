from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _create_campaign(async_client, headers, title: str):
    payload = {
        "title": title,
        "description": "rbac",
        "messages": [],
        "tags": ["rbac"],
        "active": True,
    }
    return await async_client.post("/api/v1/campaigns/", json=payload, headers=headers)


async def test_store_admin_can_create_and_get(async_client, company_a_admin_headers):
    created = await _create_campaign(async_client, company_a_admin_headers, "RBAC Store Admin")
    assert created.status_code == 201, created.text
    campaign_id = created.json().get("id")
    assert campaign_id

    fetched = await async_client.get(f"/api/v1/campaigns/{campaign_id}", headers=company_a_admin_headers)
    assert fetched.status_code == 200, fetched.text


async def test_employee_forbidden_on_create(async_client, company_a_employee_headers):
    created = await _create_campaign(async_client, company_a_employee_headers, "RBAC Employee")
    assert created.status_code == 403, created.text
    payload = created.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")


async def test_platform_admin_allowed(async_client, auth_headers):
    created = await _create_campaign(async_client, auth_headers, "RBAC Platform Admin")
    assert created.status_code == 201, created.text


async def test_store_admin_cannot_access_other_company(async_client, company_a_admin_headers, company_b_admin_headers):
    created = await _create_campaign(async_client, company_b_admin_headers, "RBAC Other Company")
    assert created.status_code == 201, created.text
    campaign_id = created.json().get("id")
    assert campaign_id

    resp = await async_client.get(f"/api/v1/campaigns/{campaign_id}", headers=company_a_admin_headers)
    assert resp.status_code == 404
