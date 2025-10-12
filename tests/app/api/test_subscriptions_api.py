import pytest

BASE = "/api/v1/subscriptions"


@pytest.mark.anyio
async def test_create_subscription_trial_ok(client, auth_headers):
    r = await client.post(
        BASE,
        json={
            "company_id": 1,
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "24900.00",
            "currency": "KZT",
            "trial_days": 7,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["company_id"] == 1
    assert data["plan"] == "Pro"
    assert data["status"] in ("trial", "active")
    assert data["next_billing_date"]


@pytest.mark.anyio
async def test_forbid_second_active_subscription(client, auth_headers):
    # первая активная
    r1 = await client.post(
        BASE,
        json={
            "company_id": 2,
            "plan": "Start",
            "billing_cycle": "monthly",
            "price": "1000.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=auth_headers,
    )
    assert r1.status_code == 201, r1.text
    # вторая активная → 409
    r2 = await client.post(
        BASE,
        json={
            "company_id": 2,
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "2000.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=auth_headers,
    )
    assert r2.status_code == 409


@pytest.mark.anyio
async def test_update_cancel_resume_renew_flow(client, auth_headers):
    r = await client.post(
        BASE,
        json={
            "company_id": 3,
            "plan": "Start",
            "billing_cycle": "yearly",
            "price": "12000.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=auth_headers,
    )
    sub = r.json()
    sid = sub["id"]

    upd = await client.patch(
        f"{BASE}/{sid}", json={"plan": "Business", "price": "33900.00"}, headers=auth_headers
    )
    assert upd.status_code == 200 and upd.json()["plan"] == "Business"

    c1 = await client.post(f"{BASE}/{sid}/cancel", headers=auth_headers)
    assert c1.status_code == 200
    c2 = await client.post(f"{BASE}/{sid}/cancel", headers=auth_headers)  # идемпотентность
    assert c2.status_code == 200

    rs = await client.post(f"{BASE}/{sid}/resume", headers=auth_headers)
    assert rs.status_code == 200 and rs.json()["status"] == "active"

    before = rs.json()["next_billing_date"]
    rn = await client.post(f"{BASE}/{sid}/renew", headers=auth_headers)
    assert rn.status_code == 200 and rn.json()["next_billing_date"] != before


@pytest.mark.anyio
async def test_current_and_filters(client, auth_headers):
    await client.post(
        BASE,
        json={
            "company_id": 4,
            "plan": "Pro",
            "billing_cycle": "monthly",
            "price": "5000.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=auth_headers,
    )

    cur = await client.get(f"{BASE}/current", params={"company_id": 4}, headers=auth_headers)
    assert cur.status_code == 200 and cur.json() is not None

    lst = await client.get(BASE, params={"company_id": 4, "plan": "Pro"}, headers=auth_headers)
    assert lst.status_code == 200 and isinstance(lst.json(), list)
