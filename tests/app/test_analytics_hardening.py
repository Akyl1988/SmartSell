from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Subscription
from app.models.company import Company
from app.models.order import Order, OrderStatus


@pytest.mark.asyncio
async def test_analytics_unauth_returns_401(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/analytics/dashboard")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_analytics_date_from_gt_date_to_returns_400_or_422(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    resp = await async_client.get(
        "/api/v1/analytics/sales",
        headers=company_a_admin_headers,
        params={"date_from": "2026-01-10", "date_to": "2026-01-01"},
    )
    assert resp.status_code in {400, 422}
    assert "detail" in resp.json()


@pytest.mark.asyncio
async def test_analytics_range_too_large_returns_400_or_422(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    resp = await async_client.get(
        "/api/v1/analytics/sales",
        headers=company_a_admin_headers,
        params={"date_from": "2020-01-01", "date_to": "2026-01-01"},
    )
    assert resp.status_code in {400, 422}
    assert "detail" in resp.json()


@pytest.mark.asyncio
@pytest.mark.no_subscription
async def test_analytics_subscription_inactive_blocked(
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
        plan="start",
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

    resp = await async_client.get("/api/v1/analytics/dashboard", headers=company_a_admin_headers)
    assert resp.status_code == 402
    assert resp.json().get("detail") == "subscription_required"


@pytest.mark.asyncio
async def test_analytics_tenant_isolation_company_a(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    company_a = (await async_db_session.execute(select(Company).where(Company.id == 1001))).scalars().first()
    if company_a is None:
        company_a = Company(id=1001, name="Company 1001")
        async_db_session.add(company_a)
        await async_db_session.flush()

    company_b = (await async_db_session.execute(select(Company).where(Company.id == 2001))).scalars().first()
    if company_b is None:
        company_b = Company(id=2001, name="Company 2001")
        async_db_session.add(company_b)
        await async_db_session.flush()

    order_a = Order(
        company_id=company_a.id,
        order_number="A-ANALYTICS-001",
        status=OrderStatus.COMPLETED,
        total_amount=Decimal("100.00"),
        created_at=datetime.utcnow(),
    )
    order_b = Order(
        company_id=company_b.id,
        order_number="B-ANALYTICS-001",
        status=OrderStatus.COMPLETED,
        total_amount=Decimal("200.00"),
        created_at=datetime.utcnow(),
    )
    async_db_session.add_all([order_a, order_b])
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/analytics/dashboard", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("total_orders") == 1
