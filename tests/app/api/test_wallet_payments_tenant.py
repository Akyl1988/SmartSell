import pytest

from app.models.user import User


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


@pytest.mark.anyio
async def test_wallet_accounts_hidden_across_companies(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    forbidden = await client.get(f"/api/v1/wallet/accounts/{account_id}", headers=company_b_admin_headers)
    assert forbidden.status_code == 404

    listed = await client.get("/api/v1/wallet/accounts", headers=company_b_admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    items = body.get("items") or body.get("data") or []
    assert all(it.get("id") != account_id for it in items)


@pytest.mark.anyio
async def test_payments_hidden_across_companies(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    acc = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc.status_code == 201, acc.text
    account_id = acc.json()["id"]

    pay = await client.post(
        "/api/v1/payments/",
        json={
            "user_id": user_a.id,
            "wallet_account_id": account_id,
            "amount": "10.00",
            "currency": "KZT",
            "reference": "isolation",
        },
        headers=company_a_admin_headers,
    )
    assert pay.status_code == 201, pay.text
    payment_id = pay.json()["id"]

    hidden = await client.get(f"/api/v1/payments/{payment_id}", headers=company_b_admin_headers)
    assert hidden.status_code == 404

    listed = await client.get("/api/v1/payments/", headers=company_b_admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    items = body.get("items") or body.get("data") or []
    assert all(it.get("id") != payment_id for it in items)


@pytest.mark.anyio
async def test_wallet_account_visible_same_company(client, db_session, company_a_admin_headers):
    """Ensure newly created wallet account is immediately readable by the same tenant."""
    user_a = _get_user_by_phone(db_session, "+70000010001")

    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    got = await client.get(f"/api/v1/wallet/accounts/{account_id}", headers=company_a_admin_headers)
    assert got.status_code == 200, got.text


@pytest.mark.anyio
async def test_wallet_ledger_hidden_across_companies(
    client, db_session, company_a_admin_headers, company_b_admin_headers
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    ledger = await client.get(f"/api/v1/wallet/accounts/{account_id}/ledger", headers=company_b_admin_headers)
    assert ledger.status_code == 404


@pytest.mark.anyio
async def test_deposit_cross_tenant_forbidden(client, db_session, company_a_admin_headers, company_b_admin_headers):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    resp = await client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "5.00", "reference": "x"},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_withdraw_cross_tenant_forbidden(client, db_session, company_a_admin_headers, company_b_admin_headers):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    # топ-up, чтобы не зависеть от баланса при проверке изоляции
    topup = await client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "10.00", "reference": "seed"},
        headers=company_a_admin_headers,
    )
    assert topup.status_code == 200, topup.text

    resp = await client.post(
        f"/api/v1/wallet/accounts/{account_id}/withdraw",
        json={"amount": "1.00", "reference": "steal"},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_transfer_cross_tenant_destination_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    user_b = _get_user_by_phone(db_session, "+70000020001")

    acc_a = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc_a.status_code == 201, acc_a.text
    account_a_id = acc_a.json()["id"]

    acc_b = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_b.id, "currency": "KZT"},
        headers=company_b_admin_headers,
    )
    assert acc_b.status_code == 201, acc_b.text
    account_b_id = acc_b.json()["id"]

    # пополняем источник, чтобы не упереться в баланс
    seed = await client.post(
        f"/api/v1/wallet/accounts/{account_a_id}/deposit",
        json={"amount": "10.00", "reference": "seed"},
        headers=company_a_admin_headers,
    )
    assert seed.status_code == 200, seed.text

    transfer = await client.post(
        "/api/v1/wallet/transfer",
        json={
            "source_account_id": account_a_id,
            "destination_account_id": account_b_id,
            "amount": "1.00",
            "reference": "cross",
        },
        headers=company_a_admin_headers,
    )
    assert transfer.status_code == 404


@pytest.mark.anyio
async def test_payment_create_cross_tenant_forbidden(
    client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    user_b = _get_user_by_phone(db_session, "+70000020001")

    acc = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc.status_code == 201, acc.text
    account_id = acc.json()["id"]

    pay = await client.post(
        "/api/v1/payments/",
        json={
            "user_id": user_b.id,
            "wallet_account_id": account_id,
            "amount": "5.00",
            "currency": "KZT",
            "reference": "cross",
        },
        headers=company_b_admin_headers,
    )
    assert pay.status_code == 404


@pytest.mark.anyio
async def test_payments_list_company_param_forbidden(
    client, db_session, company_a_admin_headers, company_b_admin_headers
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    company_a_id = user_a.company_id

    resp = await client.get(f"/api/v1/payments/?company_id={company_a_id}", headers=company_b_admin_headers)
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_wallet_list_company_param_forbidden(
    client, db_session, company_a_admin_headers, company_b_admin_headers
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    company_a_id = user_a.company_id

    resp = await client.get(f"/api/v1/wallet/accounts?company_id={company_a_id}", headers=company_b_admin_headers)
    assert resp.status_code == 403
