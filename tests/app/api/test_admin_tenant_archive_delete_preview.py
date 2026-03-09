from __future__ import annotations

import pytest

from app.models.company import Company

pytestmark = pytest.mark.asyncio


async def test_archive_delete_preview_platform_admin_success(async_client, async_db_session, auth_headers):
    company = Company(id=9701, name="Archive Preview Co", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/archive-delete-preview?action=archive",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("company_id") == company.id
    assert payload.get("requested_action") == "archive"
    assert payload.get("allowed") is True
    assert payload.get("next_state") == "archived"
    assert payload.get("destructive_delete_supported") is False


async def test_archive_delete_preview_non_platform_denied(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    company = Company(id=9702, name="Archive Preview Co 2", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/archive-delete-preview?action=archive",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body.get("code") == "ADMIN_REQUIRED"


async def test_delete_preview_requires_export_before_delete(async_client, async_db_session, auth_headers):
    company = Company(id=9703, name="Delete Preview Co", subscription_plan="pro")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/archive-delete-preview?action=delete",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("requested_action") == "delete"
    assert payload.get("allowed") is False
    assert payload.get("next_state") == "pending_export"
    assert "export_manifest_reference" in (payload.get("required_before_action") or [])
    assert "export_before_delete_required" in (payload.get("warnings") or [])
    assert payload.get("destructive_delete_supported") is False


async def test_archive_preview_allowed_transition(async_client, async_db_session, auth_headers):
    company = Company(id=9704, name="Archive Preview Co 4", subscription_plan="business")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/archive-delete-preview?action=archive",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("allowed") is True
    assert payload.get("next_state") == "archived"
