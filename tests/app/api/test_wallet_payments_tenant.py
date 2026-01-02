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
