from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dependencies as deps
from app.core.rate_limiter import RateLimiter
from app.core.security import get_password_hash
from app.models import Company, User


async def _login_and_tokens(async_client: AsyncClient, async_db_session: AsyncSession) -> dict:
    company = Company(name="Rate Limit Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="+77001239999",
        hashed_password=get_password_hash("password123"),
        role="admin",
    )
    async_db_session.add(user)
    await async_db_session.commit()

    login_data = {"identifier": "+77001239999", "password": "password123"}
    resp = await async_client.post("/api/auth/login", json=login_data)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_refresh_rate_limited(async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("REFRESH_RATE_LIMIT", "1")
    monkeypatch.setenv("REFRESH_RATE_WINDOW", "60")
    monkeypatch.setattr(deps, "_RATE_ENABLED", True)
    monkeypatch.setattr(deps, "_rate_limiter", RateLimiter(redis=None, env="test", prefix="rl"))

    tokens = await _login_and_tokens(async_client, async_db_session)

    r1 = await async_client.post("/api/auth/token/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200, r1.text

    r2 = await async_client.post("/api/auth/token/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 429
    assert r2.json().get("detail") == "auth_refresh_rate_limited"
    assert r2.headers.get("Retry-After")


@pytest.mark.asyncio
async def test_logout_rate_limited(async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("LOGOUT_RATE_LIMIT", "1")
    monkeypatch.setenv("LOGOUT_RATE_WINDOW", "60")
    monkeypatch.setattr(deps, "_RATE_ENABLED", True)
    monkeypatch.setattr(deps, "_rate_limiter", RateLimiter(redis=None, env="test", prefix="rl"))

    tokens = await _login_and_tokens(async_client, async_db_session)
    access_token = tokens["access_token"]

    r1 = await async_client.post("/api/auth/logout", headers={"Authorization": f"Bearer {access_token}"})
    assert r1.status_code == 200, r1.text

    r2 = await async_client.post("/api/auth/logout", headers={"Authorization": f"Bearer {access_token}"})
    assert r2.status_code == 429
    assert r2.json().get("detail") == "auth_logout_rate_limited"
    assert r2.headers.get("Retry-After")
