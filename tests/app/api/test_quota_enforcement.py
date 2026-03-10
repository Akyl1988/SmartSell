from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.subscriptions import plan_catalog
from app.models.billing import Subscription
from app.models.campaign import Campaign
from app.models.repricing import RepricingRule

pytestmark = pytest.mark.asyncio


async def _ensure_active_subscription(async_db_session, *, company_id: int, plan: str) -> Subscription:
    sub = (
        (
            await async_db_session.execute(
                select(Subscription).where(
                    Subscription.company_id == company_id,
                    Subscription.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if sub is None:
        now = datetime.now(UTC)
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
            grace_until=None,
        )
        async_db_session.add(sub)
    else:
        now = datetime.now(UTC)
        sub.plan = plan
        sub.status = "active"
        sub.started_at = now
        sub.period_start = now
        sub.period_end = now + timedelta(days=30)
        sub.grace_until = None
    await async_db_session.commit()
    return sub


async def _campaign_usage(async_db_session, *, company_id: int) -> int:
    return int(
        (
            await async_db_session.execute(
                select(func.count(Campaign.id)).where(Campaign.company_id == company_id, Campaign.deleted_at.is_(None))
            )
        ).scalar_one()
    )


async def _campaign_usage_via_api(async_client, headers) -> int:
    resp = await async_client.get("/api/v1/campaigns/?page=1&size=100", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    meta = payload.get("meta") or {}
    return int(meta.get("total") or 0)


async def _repricing_rule_usage(async_db_session, *, company_id: int) -> int:
    return int(
        (
            await async_db_session.execute(
                select(func.count(RepricingRule.id)).where(RepricingRule.company_id == company_id)
            )
        ).scalar_one()
    )


async def test_campaign_quota_respected_then_exceeded(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_active_subscription(async_db_session, company_id=1001, plan="start")
    baseline = await _campaign_usage_via_api(async_client, company_a_admin_headers)

    start_quotas = dict(plan_catalog.QUOTA_MATRIX.get("start", {}))
    start_quotas["campaigns"] = baseline + 1
    monkeypatch.setitem(plan_catalog.QUOTA_MATRIX, "start", start_quotas)

    first = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Quota campaign 1"},
    )
    assert first.status_code == 201, first.text

    second = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Quota campaign 2"},
    )
    assert second.status_code == 409, second.text
    assert second.json().get("code") == "QUOTA_EXCEEDED"


async def test_repricing_rule_quota_exceeded(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_active_subscription(async_db_session, company_id=1001, plan="pro")
    baseline = await _repricing_rule_usage(async_db_session, company_id=1001)

    pro_quotas = dict(plan_catalog.QUOTA_MATRIX.get("pro", {}))
    pro_quotas["repricing_rules"] = baseline + 1
    monkeypatch.setitem(plan_catalog.QUOTA_MATRIX, "pro", pro_quotas)

    payload = {
        "name": "quota-rule-1",
        "enabled": True,
        "is_active": True,
        "min_price": "10.00",
        "max_price": "200.00",
        "step": "5.00",
        "undercut": "5.00",
        "cooldown_seconds": 0,
        "max_delta_percent": "20.00",
    }

    first = await async_client.post("/api/v1/pricing/rules", json=payload, headers=company_a_admin_headers)
    assert first.status_code == 201, first.text

    second_payload = dict(payload)
    second_payload["name"] = "quota-rule-2"
    second = await async_client.post("/api/v1/pricing/rules", json=second_payload, headers=company_a_admin_headers)
    assert second.status_code == 409, second.text
    assert second.json().get("code") == "QUOTA_EXCEEDED"


async def test_plan_change_updates_campaign_quota_behavior(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    start_quotas = dict(plan_catalog.QUOTA_MATRIX.get("start", {}))
    start_quotas["campaigns"] = 1
    monkeypatch.setitem(plan_catalog.QUOTA_MATRIX, "start", start_quotas)

    pro_quotas = dict(plan_catalog.QUOTA_MATRIX.get("pro", {}))
    pro_quotas["campaigns"] = 200
    monkeypatch.setitem(plan_catalog.QUOTA_MATRIX, "pro", pro_quotas)

    await _ensure_active_subscription(async_db_session, company_id=1001, plan="start")
    baseline = await _campaign_usage_via_api(async_client, company_a_admin_headers)

    start_quotas = dict(plan_catalog.QUOTA_MATRIX.get("start", {}))
    start_quotas["campaigns"] = baseline + 1
    monkeypatch.setitem(plan_catalog.QUOTA_MATRIX, "start", start_quotas)

    pro_quotas = dict(plan_catalog.QUOTA_MATRIX.get("pro", {}))
    pro_quotas["campaigns"] = baseline + 2
    monkeypatch.setitem(plan_catalog.QUOTA_MATRIX, "pro", pro_quotas)

    first = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Plan change campaign 1"},
    )
    assert first.status_code == 201, first.text

    denied = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Plan change campaign 2"},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json().get("code") == "QUOTA_EXCEEDED"

    await _ensure_active_subscription(async_db_session, company_id=1001, plan="pro")

    allowed_after_upgrade = await async_client.post(
        "/api/v1/campaigns/",
        headers=company_a_admin_headers,
        json={"title": "Plan change campaign 2"},
    )
    assert allowed_after_upgrade.status_code == 201, allowed_after_upgrade.text
