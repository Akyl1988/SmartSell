from __future__ import annotations

import pytest

from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def test_wallet_employee_forbidden_on_create(
    async_client,
    db_session,
    company_a_employee_headers,
):
    user = _get_user_by_phone(db_session, "+70000010005")
    resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user.id, "currency": "KZT"},
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")


async def test_wallet_admin_can_create_account(async_client, db_session, company_a_admin_headers):
    user = _get_user_by_phone(db_session, "+70000010001")
    resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user.id, "currency": "USD"},
        headers=company_a_admin_headers,
    )
    assert resp.status_code in (200, 201, 409), resp.text


async def test_wallet_platform_admin_forbidden(async_client, db_session, auth_headers):
    user = _get_user_by_phone(db_session, "77000000001")
    resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user.id, "currency": "EUR"},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "FORBIDDEN"
    assert payload.get("request_id")


async def test_wallet_health_public(async_client):
    resp = await async_client.get("/api/v1/wallet/health")
    assert resp.status_code == 200, resp.text
