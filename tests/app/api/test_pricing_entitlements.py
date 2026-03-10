from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.billing import Subscription

pytestmark = pytest.mark.asyncio


async def test_pricing_feature_not_available_on_basic_plan(async_client, async_db_session, company_a_admin_headers):
    sub = (
        (
            await async_db_session.execute(
                select(Subscription).where(
                    Subscription.company_id == 1001,
                    Subscription.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    assert sub is not None
    now = datetime.now(UTC)
    sub.plan = "basic"
    sub.status = "active"
    sub.started_at = now
    sub.period_start = now
    sub.period_end = now + timedelta(days=30)
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/pricing/rules", headers=company_a_admin_headers)
    assert resp.status_code == 403, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "FEATURE_NOT_AVAILABLE"


@pytest.mark.no_subscription
async def test_pricing_without_subscription_still_returns_subscription_required(async_client, company_a_admin_headers):
    resp = await async_client.get("/api/v1/pricing/rules", headers=company_a_admin_headers)
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"
