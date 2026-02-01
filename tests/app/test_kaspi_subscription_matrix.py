from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.billing import Subscription
from app.models.company import Company

pytestmark = pytest.mark.asyncio


async def _set_plan(async_db_session, company_id: int, plan: str) -> None:
    existing_company = await async_db_session.get(Company, company_id)
    if not existing_company:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.flush()

    res = await async_db_session.execute(
        select(Subscription).where(Subscription.company_id == company_id).where(Subscription.deleted_at.is_(None))
    )
    sub = res.scalars().first()
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(
            company_id=company_id,
            plan=plan,
            status="active",
            billing_cycle="monthly",
            price=Decimal("0.00"),
            currency="KZT",
            started_at=now,
            period_start=now,
            period_end=now + timedelta(days=30),
            next_billing_date=now + timedelta(days=31),
        )
        async_db_session.add(sub)
    else:
        sub.plan = plan
        sub.status = "active"
    await async_db_session.commit()


async def test_kaspi_subscription_trial_blocks_goods_imports(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _set_plan(async_db_session, company_id=1001, plan="start")

    r = await async_client.get(
        "/api/v1/kaspi/goods/imports",
        headers=company_a_admin_headers,
    )
    assert r.status_code == 403
    payload = r.json()
    assert payload.get("detail") == "subscription_required"
    assert payload.get("code") == "subscription_required"
    assert payload.get("request_id")


async def test_kaspi_subscription_basic_allows_goods_imports_blocks_feed_uploads(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _set_plan(async_db_session, company_id=1001, plan="basic")

    r_ok = await async_client.get(
        "/api/v1/kaspi/goods/imports?limit=1",
        headers=company_a_admin_headers,
    )
    assert r_ok.status_code == 200

    r_block = await async_client.get(
        "/api/v1/kaspi/feed/uploads",
        headers=company_a_admin_headers,
    )
    assert r_block.status_code == 403


async def test_kaspi_subscription_pro_allows_feed_uploads_and_autosync(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _set_plan(async_db_session, company_id=1001, plan="pro")

    r_uploads = await async_client.get(
        "/api/v1/kaspi/feed/uploads",
        headers=company_a_admin_headers,
    )
    assert r_uploads.status_code == 200

    r_autosync = await async_client.get(
        "/api/v1/kaspi/autosync/status",
        headers=company_a_admin_headers,
    )
    assert r_autosync.status_code == 200
