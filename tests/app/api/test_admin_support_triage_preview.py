from __future__ import annotations

import pytest

from app.models.company import Company

pytestmark = pytest.mark.asyncio


async def test_support_triage_preview_platform_admin_success(async_client, async_db_session, auth_headers):
    company = Company(id=9801, name="Support Co", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/tenants/{company.id}/support-triage-preview",
        headers=auth_headers,
        json={
            "severity": "SEV-2",
            "area": "kaspi",
            "issue_summary": "Kaspi sync delay observed",
            "latest_request_id": "req_123",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload.get("company_id") == company.id
    assert payload.get("severity") == "SEV-2"
    assert payload.get("area") == "kaspi"
    assert payload.get("normalized") is True
    assert payload.get("status") == "preview"
    assert payload.get("automation_supported") is False
    assert payload.get("diagnostics_endpoint") == f"/api/v1/admin/tenants/{company.id}/diagnostics"
    assert payload.get("recommended_next_steps")


async def test_support_triage_preview_store_admin_forbidden(async_client, async_db_session, company_a_admin_headers):
    company = Company(id=9802, name="Support Co 2", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/tenants/{company.id}/support-triage-preview",
        headers=company_a_admin_headers,
        json={
            "severity": "SEV-3",
            "area": "reports",
            "issue_summary": "CSV generation question",
        },
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body.get("code") == "ADMIN_REQUIRED"


async def test_support_triage_preview_invalid_severity_rejected(async_client, async_db_session, auth_headers):
    company = Company(id=9803, name="Support Co 3", subscription_plan="pro")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/tenants/{company.id}/support-triage-preview",
        headers=auth_headers,
        json={
            "severity": "SEV-9",
            "area": "billing",
            "issue_summary": "Unexpected charge",
        },
    )
    assert resp.status_code == 422, resp.text


async def test_support_triage_preview_invalid_area_rejected(async_client, async_db_session, auth_headers):
    company = Company(id=9804, name="Support Co 4", subscription_plan="pro")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/tenants/{company.id}/support-triage-preview",
        headers=auth_headers,
        json={
            "severity": "SEV-1",
            "area": "unknown-domain",
            "issue_summary": "Critical issue",
        },
    )
    assert resp.status_code == 422, resp.text


async def test_support_triage_preview_includes_normalized_next_steps(async_client, async_db_session, auth_headers):
    company = Company(id=9805, name="Support Co 5", subscription_plan="business")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/tenants/{company.id}/support-triage-preview",
        headers=auth_headers,
        json={
            "severity": "SEV-4",
            "area": "platform",
            "issue_summary": "Minor platform question",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    steps = payload.get("recommended_next_steps") or []
    assert "fetch_diagnostics" in steps
    assert payload.get("diagnostics_endpoint") == f"/api/v1/admin/tenants/{company.id}/diagnostics"
