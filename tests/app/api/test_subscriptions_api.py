from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.billing import Subscription

BASE = "/api/v1/subscriptions"


@pytest.mark.asyncio
async def test_create_subscription_trial_ok(client, auth_headers, async_db_session):
    await async_db_session.execute(sa.delete(Subscription).where(Subscription.company_id == 1))
    await async_db_session.commit()
    r = await client.post(
        BASE,
        json={
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "24900",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["company_id"] == 1
    assert data["plan"] == "Pro"
    assert data["status"] in ("trial", "trialing")
    assert data["next_billing_date"]
    started_at = datetime.fromisoformat(data["started_at"])
    expires_at = datetime.fromisoformat(data["expires_at"])
    assert expires_at - started_at == timedelta(days=15)


@pytest.mark.asyncio
async def test_create_subscription_trial_allowed_for_non_admin_pro(client, company_a_admin_headers):
    r = await client.post(
        BASE,
        json={
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "24900",
            "currency": "KZT",
            "trial_days": 15,
        },
        headers=company_a_admin_headers,
    )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload.get("status") in ("trial", "trialing")


@pytest.mark.asyncio
async def test_create_subscription_without_trial_ok(client, company_a_admin_headers, auth_headers, async_db_session):
    r = await client.post(
        BASE,
        json={
            "plan": "Start",
            "billing_cycle": "monthly",
            "price": "0",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_a_admin_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json().get("status") == "active"

    await async_db_session.execute(sa.delete(Subscription).where(Subscription.company_id == 1))
    await async_db_session.commit()

    admin = await client.post(
        BASE,
        json={
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "24900",
            "currency": "KZT",
            "trial_days": 15,
        },
        headers=auth_headers,
    )
    assert admin.status_code == 201, admin.text
    assert admin.json().get("status") == "trial"


@pytest.mark.asyncio
async def test_forbid_second_active_subscription(client, company_a_admin_headers):
    # первая активная
    r1 = await client.post(
        BASE,
        json={
            "plan": "Start",
            "billing_cycle": "monthly",
            "price": "1000",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_a_admin_headers,
    )
    assert r1.status_code == 201, r1.text
    # вторая активная → 409
    r2 = await client.post(
        BASE,
        json={
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "2000",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_a_admin_headers,
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_update_cancel_resume_renew_flow(client, company_a_admin_headers):
    r = await client.post(
        BASE,
        json={
            "plan": "Start",
            "billing_cycle": "yearly",
            "price": "12000",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_a_admin_headers,
    )
    sub = r.json()
    sid = sub["id"]

    upd = await client.patch(
        f"{BASE}/{sid}", json={"plan": "Business", "price": "33900"}, headers=company_a_admin_headers
    )
    assert upd.status_code == 200 and upd.json()["plan"] == "Business"

    c1 = await client.post(f"{BASE}/{sid}/cancel", headers=company_a_admin_headers)
    assert c1.status_code == 200
    c2 = await client.post(f"{BASE}/{sid}/cancel", headers=company_a_admin_headers)  # идемпотентность
    assert c2.status_code == 200

    rs = await client.post(f"{BASE}/{sid}/resume", headers=company_a_admin_headers)
    assert rs.status_code == 200 and rs.json()["status"] == "active"

    before = rs.json()["next_billing_date"]
    rn = await client.post(f"{BASE}/{sid}/renew", headers=company_a_admin_headers)
    assert rn.status_code == 200 and rn.json()["next_billing_date"] != before


@pytest.mark.asyncio
async def test_current_and_filters(client, company_a_admin_headers):
    await client.post(
        BASE,
        json={
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "5000",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_a_admin_headers,
    )

    cur = await client.get(f"{BASE}/current", headers=company_a_admin_headers)
    assert cur.status_code == 200 and cur.json() is not None

    lst = await client.get(BASE, params={"plan": "Pro"}, headers=company_a_admin_headers)
    assert lst.status_code == 200 and isinstance(lst.json(), list)
