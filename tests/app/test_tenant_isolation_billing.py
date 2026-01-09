import pytest

from app.models.user import User


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_subscriptions_isolation_between_companies(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await client.post(
        "/api/v1/subscriptions",
        json={
            "plan": "Beta",
            "billing_cycle": "monthly",
            "price": "100.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text

    foreign_list = await client.get("/api/v1/subscriptions", headers=company_a_admin_headers)
    assert foreign_list.status_code == 200
    assert foreign_list.json() == []

    foreign_current = await client.get(
        "/api/v1/subscriptions/current",
        headers=company_a_admin_headers,
    )
    assert foreign_current.status_code == 200
    assert foreign_current.json() is None


@pytest.mark.asyncio
async def test_subscriptions_get_by_id_cross_company_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await client.post(
        "/api/v1/subscriptions",
        json={
            "plan": "Beta",
            "billing_cycle": "monthly",
            "price": "100.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text
    sub_id = created.json()["id"]

    # cross-tenant GET by id
    foreign_get = await client.get(
        f"/api/v1/subscriptions/{sub_id}",
        headers=company_a_admin_headers,
    )
    assert foreign_get.status_code == 404


@pytest.mark.asyncio
async def test_subscriptions_payments_cross_company_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await client.post(
        "/api/v1/subscriptions",
        json={
            "plan": "Gamma",
            "billing_cycle": "monthly",
            "price": "50.00",
            "currency": "KZT",
            "trial_days": 0,
        },
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text
    sub_id = created.json()["id"]

    payments = await client.get(
        f"/api/v1/subscriptions/{sub_id}/payments",
        headers=company_a_admin_headers,
    )
    assert payments.status_code == 404


@pytest.mark.asyncio
async def test_subscriptions_company_param_bypass_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    # company B passing company_id should not bypass scoping; list remains scoped to token company
    resp = await client.get(
        "/api/v1/subscriptions",
        params={"company_id": user_a.company_id},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_invoices_company_param_bypass_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    resp = await client.get(
        "/api/v1/invoices",
        params={"company_id": user_a.company_id},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_invoices_cross_tenant_get_by_id_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    created = await client.post(
        "/api/v1/invoices",
        json={"amount": "10.00", "currency": "KZT", "status": "draft", "description": "iso"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    inv_id = created.json()["id"]

    resp = await client.get(
        f"/api/v1/invoices/{inv_id}",
        params={"company_id": user_a.company_id},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 403 or resp.status_code == 404


@pytest.mark.asyncio
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
