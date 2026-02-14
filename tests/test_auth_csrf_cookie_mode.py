from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_csrf_token, get_password_hash
from app.models import Company, User, UserSession


async def _login_user(async_client: AsyncClient, async_db_session: AsyncSession) -> str:
    company = Company(name="CSRF Co")
    async_db_session.add(company)
    await async_db_session.flush()

    password = "S3cure!Passw0rd-2026"
    user = User(
        company_id=company.id,
        phone="+77001230001",
        email="csrf@example.com",
        hashed_password=get_password_hash(password),
        role="admin",
    )
    async_db_session.add(user)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "+77001230001", "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["refresh_token"]


async def _csrf_for_refresh(async_db_session: AsyncSession, refresh_token: str) -> str:
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    res = await async_db_session.execute(select(UserSession).where(UserSession.refresh_token == token_hash).limit(1))
    session = res.scalars().first()
    assert session is not None
    return generate_csrf_token(str(session.id))


@pytest.mark.asyncio
async def test_refresh_cookie_requires_csrf(async_client: AsyncClient, async_db_session: AsyncSession):
    refresh_token = await _login_user(async_client, async_db_session)

    resp = await async_client.post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": refresh_token},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_refresh_cookie_accepts_csrf(async_client: AsyncClient, async_db_session: AsyncSession):
    refresh_token = await _login_user(async_client, async_db_session)
    csrf_token = await _csrf_for_refresh(async_db_session, refresh_token)

    resp = await async_client.post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": refresh_token},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("access_token")
    assert payload.get("refresh_token")


@pytest.mark.asyncio
async def test_logout_cookie_requires_csrf(async_client: AsyncClient, async_db_session: AsyncSession):
    refresh_token = await _login_user(async_client, async_db_session)

    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={"refresh_token": refresh_token},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_logout_cookie_accepts_csrf(async_client: AsyncClient, async_db_session: AsyncSession):
    refresh_token = await _login_user(async_client, async_db_session)
    csrf_token = await _csrf_for_refresh(async_db_session, refresh_token)

    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={"refresh_token": refresh_token},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200, resp.text
