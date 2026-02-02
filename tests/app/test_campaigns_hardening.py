from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.billing import Subscription
from app.models.company import Company


@pytest.mark.asyncio
async def test_campaign_invalid_inputs_return_422(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    blank_title = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "   "},
    )
    assert blank_title.status_code == 422

    bad_schedule = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Valid Title", "schedule": "not-a-date"},
    )
    assert bad_schedule.status_code == 422


@pytest.mark.asyncio
async def test_campaign_update_invalid_schedule_422(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Schedule Test"},
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json().get("id")
    assert campaign_id

    bad_update = await async_client.put(
        f"/api/v1/campaigns/{campaign_id}",
        headers=company_a_admin_headers,
        json={"title": "Schedule Test", "schedule": "bad"},
    )
    assert bad_update.status_code == 422


@pytest.mark.asyncio
async def test_campaign_duplicate_title_same_tenant_409(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    first = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Duplicate Title"},
    )
    assert first.status_code == 201, first.text

    second = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Duplicate Title"},
    )
    assert second.status_code == 409
    assert second.json().get("detail") == "Campaign with this title already exists"


@pytest.mark.asyncio
async def test_campaign_duplicate_title_cross_tenant_ok(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
    company_b_admin_headers,
):
    first = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Cross Tenant Title"},
    )
    assert first.status_code == 201, first.text

    second = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_b_admin_headers,
        json={"title": "Cross Tenant Title"},
    )
    assert second.status_code == 201, second.text


@pytest.mark.asyncio
async def test_campaign_subscription_active_allows_access(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    resp = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Subscription Active"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
@pytest.mark.no_subscription
async def test_campaign_subscription_inactive_blocks_access(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    now = datetime.now(UTC)
    company = (await async_db_session.execute(select(Company).where(Company.id == 1001))).scalars().first()
    if company is None:
        company = Company(id=1001, name="Company 1001")
        async_db_session.add(company)
        await async_db_session.flush()

    sub = Subscription(
        company_id=company.id,
        plan=normalize_plan_id("start") or "trial",
        status="canceled",
        billing_cycle="monthly",
        price=Decimal("0.00"),
        currency="KZT",
        started_at=now,
        period_start=now,
        period_end=now + timedelta(days=30),
        next_billing_date=now + timedelta(days=31),
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Subscription Inactive"},
    )
    assert resp.status_code == 402
    assert resp.json().get("detail") == "subscription_required"
