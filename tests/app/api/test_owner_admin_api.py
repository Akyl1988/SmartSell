from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company


@pytest.mark.asyncio
async def test_admin_invite_requires_platform_admin(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
):
    company = Company(id=9001, name="Invite Block Co")
    async_db_session.add(company)
    await async_db_session.flush()
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/admin/invites",
        headers=company_a_admin_headers,
        json={"company_id": company.id, "phone": "77007770011", "grace_days": 7, "initial_plan": "trial_pro"},
    )
    assert resp.status_code == 403
    await async_db_session.rollback()


@pytest.mark.asyncio
async def test_admin_invite_success(async_client: AsyncClient, async_db_session: AsyncSession, auth_headers):
    company = Company(id=9002, name="Invite Ok Co")
    async_db_session.add(company)
    await async_db_session.flush()
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/admin/invites",
        headers=auth_headers,
        json={"company_id": company.id, "phone": "77007770022", "grace_days": 7, "initial_plan": "trial_pro"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "invite_url" in body
    assert "/invite?token=" in body["invite_url"]
    assert body.get("company_id") == company.id
    assert body.get("otp_grace_until")
    await async_db_session.rollback()


@pytest.mark.asyncio
async def test_platform_summary(async_client: AsyncClient, auth_headers):
    resp = await async_client.get("/api/v1/admin/platform/summary", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "companies_total" in body
    assert "companies_active" in body
    assert "subscriptions" in body
    assert "wallet" in body
    assert "health" in body


@pytest.mark.asyncio
async def test_admin_companies_create_list_detail(
    async_client: AsyncClient, auth_headers, async_db_session: AsyncSession
):
    await async_db_session.execute(
        text("SELECT setval('companies_id_seq', (SELECT COALESCE(MAX(id), 1) FROM companies))")
    )
    await async_db_session.commit()
    resp = await async_client.post(
        "/api/v1/admin/companies",
        headers=auth_headers,
        json={"name": "Owner Co", "bin_iin": "123456789012"},
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()
    company_id = created["id"]

    list_resp = await async_client.get("/api/v1/admin/companies?page=1&size=20", headers=auth_headers)
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json().get("items", [])
    assert any(item["id"] == company_id for item in items)

    detail_resp = await async_client.get(f"/api/v1/admin/companies/{company_id}", headers=auth_headers)
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["id"] == company_id
    assert "admins" in detail


@pytest.mark.asyncio
async def test_admin_subscriptions_set_plan_extend_and_list(
    async_client: AsyncClient, auth_headers, async_db_session: AsyncSession
):
    company = Company(id=9003, name="Plan Co")
    async_db_session.add(company)
    await async_db_session.flush()
    await async_db_session.commit()

    set_resp = await async_client.post(
        f"/api/v1/admin/subscriptions/{company.id}/set-plan",
        headers=auth_headers,
        json={"plan": "start", "reason": "setup"},
    )
    assert set_resp.status_code == 200, set_resp.text

    extend_resp = await async_client.post(
        f"/api/v1/admin/subscriptions/{company.id}/extend",
        headers=auth_headers,
        json={"days": 5, "reason": "onboarding"},
    )
    assert extend_resp.status_code == 200, extend_resp.text

    list_resp = await async_client.get("/api/v1/admin/subscriptions/stores", headers=auth_headers)
    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    assert any(row["company_id"] == company.id for row in rows)
    await async_db_session.rollback()
