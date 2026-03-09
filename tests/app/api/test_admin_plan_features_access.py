from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.billing import Subscription
from app.models.subscription_catalog import Plan

pytestmark = pytest.mark.asyncio


async def test_plan_feature_toggle_affects_pricing_access(
    async_client,
    async_db_session,
    auth_headers,
    company_a_admin_headers,
):
    create_plan = await async_client.post(
        "/api/v1/admin/plans",
        headers=auth_headers,
        json={
            "code": "basic",
            "name": "Basic",
            "price": "0",
            "currency": "KZT",
            "is_active": True,
            "trial_days_default": 14,
        },
    )
    assert create_plan.status_code in {201, 409}, create_plan.text

    plan = (await async_db_session.execute(select(Plan).where(Plan.code == "basic"))).scalars().first()
    assert plan is not None

    sub = (
        (await async_db_session.execute(select(Subscription).where(Subscription.company_id == 1001))).scalars().first()
    )
    assert sub is not None
    sub.plan = "basic"
    await async_db_session.commit()

    disable_feature = await async_client.put(
        "/api/v1/admin/plan-features/basic/repricing",
        headers=auth_headers,
        json={"enabled": False, "limits": {}},
    )
    assert disable_feature.status_code == 200, disable_feature.text

    denied = await async_client.get("/api/v1/pricing/rules", headers=company_a_admin_headers)
    assert denied.status_code == 403, denied.text
    detail = denied.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "FEATURE_NOT_AVAILABLE"

    enable_feature = await async_client.put(
        "/api/v1/admin/plan-features/basic/repricing",
        headers=auth_headers,
        json={"enabled": True, "limits": {}},
    )
    assert enable_feature.status_code == 200, enable_feature.text

    allowed = await async_client.get("/api/v1/pricing/rules", headers=company_a_admin_headers)
    assert allowed.status_code == 200, allowed.text
