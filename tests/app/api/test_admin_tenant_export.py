from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.models.company import Company

pytestmark = pytest.mark.asyncio


async def test_admin_tenant_export_manifest_platform_admin_ok(async_client, async_db_session, auth_headers):
    company = Company(
        id=9601,
        name="Export Store",
        subscription_plan="pro",
        kaspi_api_key="SUPER_SECRET_KEY",
        settings=json.dumps({"provider_secret": "TOP_SECRET_VALUE"}, ensure_ascii=False),
    )
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/export",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload.get("company_id") == company.id
    assert payload.get("company_name") == company.name
    assert payload.get("exported_at")
    datetime.fromisoformat(payload.get("exported_at").replace("Z", "+00:00"))
    assert payload.get("included_sections")
    assert payload.get("section_counts")
    assert payload.get("warnings")
    assert payload.get("not_included")

    counts = payload.get("section_counts") or {}
    assert isinstance(counts, dict)
    assert all(int(v) >= 0 for v in counts.values())

    raw_payload = json.dumps(payload, ensure_ascii=False)
    assert "SUPER_SECRET_KEY" not in raw_payload
    assert "TOP_SECRET_VALUE" not in raw_payload


async def test_admin_tenant_export_manifest_forbidden_for_store_admin(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    company = Company(id=9602, name="Export Store 2", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/export",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"


async def test_admin_tenant_export_manifest_forbidden_for_tenant_admin(
    async_client,
    async_db_session,
    company_b_admin_headers,
):
    company = Company(id=9603, name="Export Store 3", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/export",
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
