from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.billing import Subscription
from app.models.order import Order

pytestmark = pytest.mark.asyncio


def _payload():
    return {
        "currency": "KZT",
        "customer_name": "Alice",
        "customer_phone": "+77000000000",
        "notes": "call before delivery",
        "items": [
            {
                "sku": "SKU-001",
                "name": "Test Item",
                "qty": 2,
                "price": "100.00",
            }
        ],
    }


async def test_preorders_employee_forbidden(
    async_client,
    company_a_employee_headers,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_employee_headers,
    )
    assert created.status_code == 403, created.text

    listed = await async_client.get("/api/v1/preorders", headers=company_a_employee_headers)
    assert listed.status_code == 403, listed.text

    forbidden = await async_client.get("/api/v1/preorders/1", headers=company_a_employee_headers)
    assert forbidden.status_code == 403, forbidden.text

    confirm = await async_client.post("/api/v1/preorders/1/confirm", headers=company_a_employee_headers)
    assert confirm.status_code == 403, confirm.text


async def test_preorders_store_admin_flow_and_tenant_isolation(
    async_client,
    company_a_admin_headers,
    company_b_admin_headers,
    async_db_session,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")
    assert preorder_id

    listed = await async_client.get("/api/v1/preorders", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    items = listed.json().get("items") or []
    assert any(item.get("id") == preorder_id for item in items)

    fetched = await async_client.get(f"/api/v1/preorders/{preorder_id}", headers=company_a_admin_headers)
    assert fetched.status_code == 200, fetched.text
    assert isinstance(fetched.json().get("items"), list)

    forbidden = await async_client.get(f"/api/v1/preorders/{preorder_id}", headers=company_b_admin_headers)
    assert forbidden.status_code == 404, forbidden.text

    updated = await async_client.patch(
        f"/api/v1/preorders/{preorder_id}",
        json={"notes": "updated"},
        headers=company_a_admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json().get("notes") == "updated"

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("status") == "confirmed"

    before_count = int((await async_db_session.execute(select(func.count()).select_from(Order))).scalar_one())
    fulfilled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    fulfilled_payload = fulfilled.json()
    assert fulfilled_payload.get("status") == "fulfilled"
    first_order_id = fulfilled_payload.get("fulfilled_order_id")
    assert first_order_id

    fulfilled_again = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled_again.status_code == 409, fulfilled_again.text
    assert fulfilled_again.json().get("code") == "PREORDER_ALREADY_FULFILLED"
    after_count = int((await async_db_session.execute(select(func.count()).select_from(Order))).scalar_one())
    assert after_count == before_count + 1


async def test_preorders_transitions_invalid(
    async_client,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")
    assert preorder_id

    fulfilled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 422, fulfilled.text

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    updated = await async_client.patch(
        f"/api/v1/preorders/{preorder_id}",
        json={"notes": "should fail"},
        headers=company_a_admin_headers,
    )
    assert updated.status_code == 409, updated.text

    cancelled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json().get("status") == "cancelled"

    cancelled_again = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled_again.status_code == 200, cancelled_again.text
    assert cancelled_again.json().get("status") == "cancelled"


async def test_preorders_fulfill_requires_item_price(
    async_client,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {"sku": "SKU-NULL", "name": "No Price", "qty": 1, "price": None},
            ],
        },
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")
    assert preorder_id

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    fulfilled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 422, fulfilled.text


@pytest.mark.no_subscription
async def test_preorders_require_subscription(async_client, company_a_admin_headers):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 402, created.text
    detail = created.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


async def test_preorders_blocked_on_basic_plan(async_client, async_db_session, company_a_admin_headers):
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
    sub.plan = "basic"
    sub.status = "active"
    now = datetime.now(UTC)
    sub.started_at = now
    sub.period_start = now
    sub.period_end = now + timedelta(days=30)
    await async_db_session.commit()

    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 403, created.text
    detail = created.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "FEATURE_NOT_AVAILABLE"


async def test_preorders_blocked_after_trial_expiry(async_client, async_db_session, company_a_admin_headers):
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

    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 402, created.text
    detail = created.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"
