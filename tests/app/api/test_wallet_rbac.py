from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import get_password_hash

from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


def _get_platform_admin(db_session) -> User:
    user = db_session.query(User).filter(User.role == "platform_admin").first()
    if user:
        return user
    base_user = db_session.query(User).first()
    assert base_user is not None
    phone = "+" + str(70000000000 + (uuid4().int % 1000000000))
    user = User(
        phone=phone,
        company_id=base_user.company_id,
        hashed_password=get_password_hash("Secret123!"),
        role="platform_admin",
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


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
    user = _get_platform_admin(db_session)
    resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user.id, "currency": "EUR"},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "FORBIDDEN"
    assert payload.get("request_id")


async def test_wallet_platform_admin_forbidden_for_other_user(async_client, db_session, auth_headers):
    platform_user = _get_platform_admin(db_session)
    other_user = User(
        phone="+" + str(79000000000 + (uuid4().int % 1000000000)),
        company_id=platform_user.company_id,
        hashed_password=get_password_hash("Secret123!"),
        role="employee",
        is_active=True,
        is_verified=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": other_user.id, "currency": "KZT"},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "FORBIDDEN"
    assert payload.get("request_id")


async def test_wallet_health_public(async_client):
    resp = await async_client.get("/api/v1/wallet/health")
    assert resp.status_code == 200, resp.text
