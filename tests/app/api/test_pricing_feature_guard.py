from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.billing import Subscription

pytestmark = pytest.mark.asyncio


async def test_pricing_blocked_after_trial_expiry(async_client, async_db_session, company_a_admin_headers):
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
    sub.plan = "pro"
    sub.status = "trialing"
    now = datetime.now(UTC)
    sub.started_at = now - timedelta(days=16)
    sub.period_start = sub.started_at
    sub.period_end = now - timedelta(days=1)
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/pricing/rules", headers=company_a_admin_headers)
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"
