import pytest

from app.core.security import get_password_hash
from app.models.user import User


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


def _get_platform_admin(db_session) -> User:
    return db_session.query(User).filter(User.phone.in_(["77000000001", "+77000000001"])).one()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
            "amount": "10",
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


@pytest.mark.asyncio
async def test_platform_admin_can_access_own_wallet_accounts(client, db_session, auth_headers):
    user = _get_platform_admin(db_session)

    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user.id, "currency": "KZT"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    by_user = await client.get(
        f"/api/v1/wallet/accounts/by-user?user_id={user.id}&currency=KZT",
        headers=auth_headers,
    )
    assert by_user.status_code == 200, by_user.text
    assert by_user.json().get("id") == account_id

    listed = await client.get(
        f"/api/v1/wallet/accounts?user_id={user.id}&currency=KZT",
        headers=auth_headers,
    )
    assert listed.status_code == 200, listed.text
    items = listed.json().get("items") or []
    assert any(it.get("id") == account_id for it in items)


@pytest.mark.asyncio
async def test_platform_admin_forbidden_for_other_user_wallet(client, db_session, auth_headers):
    platform_user = _get_platform_admin(db_session)
    other_user = User(
        phone="+77000009999",
        company_id=platform_user.company_id,
        hashed_password=get_password_hash("Secret123!"),
        role="employee",
        is_active=True,
        is_verified=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    listed = await client.get(
        f"/api/v1/wallet/accounts?user_id={other_user.id}&currency=KZT",
        headers=auth_headers,
    )
    assert listed.status_code == 403, listed.text

    by_user = await client.get(
        f"/api/v1/wallet/accounts/by-user?user_id={other_user.id}&currency=KZT",
        headers=auth_headers,
    )
    assert by_user.status_code == 403, by_user.text


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
        json={"amount": "5", "reference": "x"},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
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
        json={"amount": "10", "reference": "seed"},
        headers=company_a_admin_headers,
    )
    assert topup.status_code == 200, topup.text

    resp = await client.post(
        f"/api/v1/wallet/accounts/{account_id}/withdraw",
        json={"amount": "1", "reference": "steal"},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
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
        json={"amount": "10", "reference": "seed"},
        headers=company_a_admin_headers,
    )
    assert seed.status_code == 200, seed.text

    transfer = await client.post(
        "/api/v1/wallet/transfer",
        json={
            "source_account_id": account_a_id,
            "destination_account_id": account_b_id,
            "amount": "1",
            "reference": "cross",
        },
        headers=company_a_admin_headers,
    )
    assert transfer.status_code == 404


@pytest.mark.asyncio
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
            "amount": "5",
            "currency": "KZT",
            "reference": "cross",
        },
        headers=company_b_admin_headers,
    )
    assert pay.status_code == 404


@pytest.mark.asyncio
async def test_payments_list_scoped_by_token(
    client, db_session, company_a_admin_headers, company_b_admin_headers, auth_headers
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
            "amount": "5",
            "currency": "KZT",
            "reference": "scoped",
        },
        headers=company_a_admin_headers,
    )
    assert pay.status_code == 201, pay.text
    payment_id = pay.json()["id"]

    allowed = await client.get("/api/v1/payments/", headers=company_a_admin_headers)
    assert allowed.status_code == 200, allowed.text
    allowed_items = allowed.json().get("items") or allowed.json().get("data") or []
    assert any(it.get("id") == payment_id for it in allowed_items)

    foreign = await client.get("/api/v1/payments/", headers=company_b_admin_headers)
    assert foreign.status_code == 200, foreign.text
    foreign_items = foreign.json().get("items") or foreign.json().get("data") or []
    assert all(it.get("id") != payment_id for it in foreign_items)

    platform = await client.get("/api/v1/payments/", headers=auth_headers)
    assert platform.status_code == 403, platform.text


@pytest.mark.asyncio
async def test_wallet_list_scoped_by_token(
    client, db_session, company_a_admin_headers, company_b_admin_headers, auth_headers
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    created = await client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    allowed = await client.get("/api/v1/wallet/accounts", headers=company_a_admin_headers)
    assert allowed.status_code == 200, allowed.text
    allowed_items = allowed.json().get("items") or allowed.json().get("data") or []
    assert any(it.get("id") == account_id for it in allowed_items)

    foreign = await client.get("/api/v1/wallet/accounts", headers=company_b_admin_headers)
    assert foreign.status_code == 200, foreign.text
    foreign_items = foreign.json().get("items") or foreign.json().get("data") or []
    assert all(it.get("id") != account_id for it in foreign_items)

    platform = await client.get("/api/v1/wallet/accounts", headers=auth_headers)
    assert platform.status_code == 403, platform.text
