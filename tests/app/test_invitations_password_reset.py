import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models import Company, InvitationToken, PasswordResetToken, User
from app.utils.tokens import hash_token


@pytest.mark.asyncio
async def test_invitation_create_requires_admin(async_client: AsyncClient, company_a_manager_headers):
    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=company_a_manager_headers,
        json={"email": "inv@example.com", "phone": "77001112233", "role": "employee"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invitation_owner_can_invite_admin(
    async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    company = Company(name="Owner Invite Co")
    async_db_session.add(company)
    await async_db_session.flush()

    owner = User(
        company_id=company.id,
        phone="77001112211",
        email="owner@example.com",
        hashed_password=get_password_hash("Password123!"),
        role="admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(owner)
    await async_db_session.commit()
    company.owner_id = owner.id
    await async_db_session.commit()

    from app.core.security import create_access_token
    from app.models.user import UserSession

    session = UserSession(
        user_id=owner.id,
        refresh_token=f"rt-{owner.id}-{secrets.token_urlsafe(8)}",
        is_active=True,
    )
    async_db_session.add(session)
    await async_db_session.commit()
    await async_db_session.refresh(session)
    token = create_access_token(
        subject=owner.id,
        extra={"company_id": company.id, "role": owner.role, "sid": session.id},
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=headers,
        json={"email": "admin-invite@example.com", "phone": "77001112212", "role": "admin"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_invitation_admin_cannot_invite_admin(async_client: AsyncClient, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=company_a_admin_headers,
        json={"email": "admin2@example.com", "phone": "77001112234", "role": "admin"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invitation_admin_can_invite_employee(async_client: AsyncClient, company_a_admin_headers, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=company_a_admin_headers,
        json={"email": "employee1@example.com", "phone": "77001112235", "role": "employee"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_invitation_employee_cannot_invite(
    async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    company = Company(name="Employee Invite Co")
    async_db_session.add(company)
    await async_db_session.flush()

    employee = User(
        company_id=company.id,
        phone="77001112236",
        email="employee@example.com",
        hashed_password=get_password_hash("Password123!"),
        role="employee",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(employee)
    await async_db_session.commit()

    from app.core.security import create_access_token
    from app.models.user import UserSession

    session = UserSession(
        user_id=employee.id,
        refresh_token=f"rt-{employee.id}-{secrets.token_urlsafe(8)}",
        is_active=True,
    )
    async_db_session.add(session)
    await async_db_session.commit()
    await async_db_session.refresh(session)
    token = create_access_token(
        subject=employee.id,
        extra={"company_id": company.id, "role": employee.role, "sid": session.id},
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=headers,
        json={"email": "other@example.com", "phone": "77001112237", "role": "employee"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invitation_accept_creates_user_without_otp(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=company_a_admin_headers,
        json={"email": "newuser@example.com", "phone": "77001112233", "role": "employee"},
    )
    assert resp.status_code == 200, resp.text
    rows = await async_db_session.execute(select(InvitationToken).order_by(InvitationToken.id.desc()))
    invite = rows.scalars().first()
    assert invite is not None
    token = "invitation-token-123456"
    invite.token_hash = hash_token(token)
    await async_db_session.commit()

    accept = await async_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Password123!"},
    )
    assert accept.status_code == 200, accept.text
    body = accept.json()
    assert body.get("access_token")

    await async_db_session.rollback()
    res = await async_db_session.execute(select(User).where(User.phone == "77001112233"))
    user = res.scalars().first()
    assert user is not None
    assert user.company_id is not None
    assert user.is_verified is False


@pytest.mark.asyncio
async def test_invitation_expired_rejected(async_client: AsyncClient, async_db_session: AsyncSession):
    company = Company(name="Invite Co")
    async_db_session.add(company)
    await async_db_session.flush()

    token = "expired-token"
    inv = InvitationToken.build(
        company_id=company.id,
        role="employee",
        phone="77009990000",
        email="exp@example.com",
        token_hash=hash_token(token),
        ttl_hours=72,
    )
    inv.expires_at = inv.expires_at.replace(year=2000)
    async_db_session.add(inv)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Password123!"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invitation_used_rejected(async_client: AsyncClient, async_db_session: AsyncSession):
    company = Company(name="Invite Used Co")
    async_db_session.add(company)
    await async_db_session.flush()

    token = "used-token"
    inv = InvitationToken.build(
        company_id=company.id,
        role="employee",
        phone="77009990001",
        email="used@example.com",
        token_hash=hash_token(token),
        ttl_hours=72,
    )
    inv.used_at = inv.created_at
    async_db_session.add(inv)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Password123!"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_password_reset_request_does_not_leak_user_existence(
    async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = await async_client.post(
        "/api/v1/auth/password/reset/request",
        json={"identifier": "unknown@example.com"},
    )
    assert resp.status_code == 200

    company = Company(name="Reset Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="77007770000",
        email="reset@example.com",
        hashed_password=get_password_hash("Password123!"),
        role="admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()

    resp2 = await async_client.post(
        "/api/v1/auth/password/reset/request",
        json={"identifier": "reset@example.com"},
    )
    assert resp2.status_code == 200

    await async_db_session.rollback()
    rows = await async_db_session.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    token_row = rows.scalars().first()
    assert token_row is not None


@pytest.mark.asyncio
async def test_password_reset_confirm_works_and_is_one_time(async_client: AsyncClient, async_db_session: AsyncSession):
    company = Company(name="Reset Confirm Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="77007770001",
        email="reset-confirm@example.com",
        hashed_password=get_password_hash("Password123!"),
        role="admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()

    token = "reset-token-123456"
    reset = PasswordResetToken.build(user_id=user.id, token_hash=hash_token(token), ttl_minutes=10)
    async_db_session.add(reset)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": token, "new_password": "NewPassword123!"},
    )
    assert resp.status_code == 200

    resp2 = await async_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": token, "new_password": "AnotherPassword123!"},
    )
    assert resp2.status_code == 422
