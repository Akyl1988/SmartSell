from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models import Company, User


@pytest.mark.asyncio
async def test_logout_ignores_invalid_access_token(async_client: AsyncClient, async_db_session: AsyncSession):
    company = Company(name="Logout Co")
    async_db_session.add(company)
    await async_db_session.flush()

    password = "S3cure!Passw0rd-2026"
    user = User(
        company_id=company.id,
        phone="+77001239999",
        email="logout@example.com",
        hashed_password=get_password_hash(password),
        role="admin",
    )
    async_db_session.add(user)
    await async_db_session.commit()

    login_data = {"identifier": "+77001239999", "password": password}
    login_resp = await async_client.post("/api/v1/auth/login", json=login_data)
    assert login_resp.status_code == 200, login_resp.text
    refresh_token = login_resp.json()["refresh_token"]

    resp = await async_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": "Bearer invalid.token.value"},
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200, resp.text
