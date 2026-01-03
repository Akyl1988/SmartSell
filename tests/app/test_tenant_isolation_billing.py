import pytest

from app.models.user import User


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


@pytest.mark.anyio
async def test_wallet_isolation_between_companies(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_b = _get_user_by_phone(db_session, "+70000020001")

    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_b.id, "currency": "KZT"},
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    seeded = await client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "5.00", "reference": "seed"},
        headers=company_b_admin_headers,
    )
    assert seeded.status_code == 200, seeded.text

    foreign_acc = await client.get(f"/api/v1/wallet/accounts/{account_id}", headers=company_a_admin_headers)
    assert foreign_acc.status_code == 404

    foreign_ledger = await client.get(
        f"/api/v1/wallet/accounts/{account_id}/ledger",
        headers=company_a_admin_headers,
    )
    assert foreign_ledger.status_code == 404

    listed = await client.get("/api/v1/wallet/accounts", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["meta"]["total"] == 0


@pytest.mark.anyio
async def test_payments_isolation_between_companies(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_b = _get_user_by_phone(db_session, "+70000020001")

    acc = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_b.id, "currency": "KZT"},
        headers=company_b_admin_headers,
    )
    assert acc.status_code == 201, acc.text
    account_id = acc.json()["id"]

    payment = await client.post(
        "/api/v1/payments/",
        json={
            "user_id": user_b.id,
            "wallet_account_id": account_id,
            "amount": "10.00",
            "currency": "KZT",
            "reference": "tenant-iso",
        },
        headers=company_b_admin_headers,
    )
    assert payment.status_code == 201, payment.text
    payment_id = payment.json()["id"]

    hidden = await client.get(f"/api/v1/payments/{payment_id}", headers=company_a_admin_headers)
    assert hidden.status_code == 404

    listed = await client.get("/api/v1/payments/", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["meta"]["total"] == 0


@pytest.mark.anyio
async def test_subscriptions_isolation_between_companies(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_b = _get_user_by_phone(db_session, "+70000020001")

    created = await client.post(
        "/api/v1/subscriptions",
        json={
            "company_id": user_b.company_id,
            "plan": "Beta",
            "billing_cycle": "monthly",
            "price": "100.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text

    foreign_list = await client.get(
        "/api/v1/subscriptions",
        params={"company_id": user_b.company_id},
        headers=company_a_admin_headers,
    )
    assert foreign_list.status_code == 404

    foreign_current = await client.get(
        "/api/v1/subscriptions/current",
        params={"company_id": user_b.company_id},
        headers=company_a_admin_headers,
    )
    assert foreign_current.status_code == 404


@pytest.mark.anyio
async def test_rbac_roles_restricted_actions(
    client,
    db_session,
    company_a_admin_headers,
    company_a_analyst_headers,
    company_a_storekeeper_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    # Admin creates wallet account in own tenant
    acc = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc.status_code == 201, acc.text
    account_id = acc.json()["id"]

    # Analyst cannot create wallet account
    denied_wallet = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_analyst_headers,
    )
    assert denied_wallet.status_code in (401, 403)

    # Storekeeper cannot deposit
    denied_deposit = await client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "5.00", "reference": "nope"},
        headers=company_a_storekeeper_headers,
    )
    assert denied_deposit.status_code in (401, 403)

    # Storekeeper cannot create payments
    denied_payment = await client.post(
        "/api/v1/payments/",
        json={
            "user_id": user_a.id,
            "wallet_account_id": account_id,
            "amount": "5.00",
            "currency": "KZT",
            "reference": "rbac",
        },
        headers=company_a_storekeeper_headers,
    )
    assert denied_payment.status_code in (401, 403)

    # Admin can create payment within own tenant
    allowed_payment = await client.post(
        "/api/v1/payments/",
        json={
            "user_id": user_a.id,
            "wallet_account_id": account_id,
            "amount": "5.00",
            "currency": "KZT",
            "reference": "ok",
        },
        headers=company_a_admin_headers,
    )
    assert allowed_payment.status_code == 201, allowed_payment.text
