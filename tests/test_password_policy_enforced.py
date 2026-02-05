from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models import Company, InvitationToken, PasswordResetToken, User
from app.utils.tokens import hash_token


WEAK_PASSWORD = "password1234"
STRONG_PASSWORD = "StrongPass123!"


def _enable_otp_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTP_PROVIDER", "mobizon")
    monkeypatch.setenv("OTP_ENABLED", "1")


@pytest.mark.asyncio
async def test_password_policy_enforced_on_register(
    async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    _enable_otp_provider(monkeypatch)

    weak_payload = {
        "phone": "+77001230001",
        "password": WEAK_PASSWORD,
        "company_name": "Weak Co",
    }
    resp = await async_client.post("/api/v1/auth/register", json=weak_payload)
    assert resp.status_code == 400
    assert resp.json().get("detail") == "password_policy_violation"

    strong_payload = {
        "phone": "+77001230002",
        "password": STRONG_PASSWORD,
        "company_name": "Strong Co",
    }
    resp2 = await async_client.post("/api/v1/auth/register", json=strong_payload)
    assert resp2.status_code == 200, resp2.text


@pytest.mark.asyncio
async def test_password_policy_enforced_on_password_change(
    async_client: AsyncClient, async_db_session: AsyncSession
):
    company = Company(name="Change Pw Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="77009990010",
        email="change@example.com",
        hashed_password=get_password_hash(STRONG_PASSWORD),
        role="admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()

    token = create_access_token(subject=user.id, extra={"company_id": company.id, "role": user.role})
    headers = {"Authorization": f"Bearer {token}"}

    weak = {"current_password": STRONG_PASSWORD, "new_password": WEAK_PASSWORD}
    resp = await async_client.post("/api/v1/auth/change-password", json=weak, headers=headers)
    assert resp.status_code == 400
    assert resp.json().get("detail") == "password_policy_violation"

    strong = {"current_password": STRONG_PASSWORD, "new_password": "AnotherStrong123!"}
    resp2 = await async_client.post("/api/v1/auth/change-password", json=strong, headers=headers)
    assert resp2.status_code == 200, resp2.text


@pytest.mark.asyncio
async def test_password_policy_enforced_on_invite_accept(
    async_client: AsyncClient, async_db_session: AsyncSession
):
    company = Company(name="Invite Policy Co")
    async_db_session.add(company)
    await async_db_session.flush()

    token = "invite-token-weak"
    inv = InvitationToken.build(
        company_id=company.id,
        role="employee",
        phone="77001110011",
        email="invite-policy@example.com",
        token_hash=hash_token(token),
        ttl_hours=72,
    )
    async_db_session.add(inv)
    await async_db_session.commit()

    weak = await async_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": WEAK_PASSWORD},
    )
    assert weak.status_code == 400
    assert weak.json().get("detail") == "password_policy_violation"

    strong = await async_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": STRONG_PASSWORD},
    )
    assert strong.status_code == 200, strong.text

    await async_db_session.rollback()
    res = await async_db_session.execute(select(User).where(User.email == "invite-policy@example.com"))
    assert res.scalars().first() is not None


@pytest.mark.asyncio
async def test_password_policy_enforced_on_password_reset_confirm(
    async_client: AsyncClient, async_db_session: AsyncSession
):
    company = Company(name="Reset Policy Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="77007770002",
        email="reset-policy@example.com",
        hashed_password=get_password_hash(STRONG_PASSWORD),
        role="admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()

    token = "reset-policy-token"
    reset = PasswordResetToken.build(user_id=user.id, token_hash=hash_token(token), ttl_minutes=10)
    async_db_session.add(reset)
    await async_db_session.commit()

    weak = await async_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": token, "new_password": WEAK_PASSWORD},
    )
    assert weak.status_code == 400
    assert weak.json().get("detail") == "password_policy_violation"

    strong = await async_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": token, "new_password": STRONG_PASSWORD},
    )
    assert strong.status_code == 200, strong.text
