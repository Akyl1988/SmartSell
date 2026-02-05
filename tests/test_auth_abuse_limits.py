from __future__ import annotations

import pytest

from app.core import dependencies as deps
from app.core.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_otp_per_phone_rate_limit(async_client, monkeypatch):
    monkeypatch.setenv("OTP_PHONE_RATE_LIMIT", "2")
    monkeypatch.setenv("OTP_PHONE_RATE_WINDOW", "60")
    monkeypatch.setattr(deps, "_RATE_ENABLED", True)
    monkeypatch.setattr(deps, "_rate_limiter", RateLimiter(redis=None, env="test", prefix="rl"))

    payload = {"phone": "+77001234567", "purpose": "login"}

    r1 = await async_client.post("/api/v1/auth/request-otp", json=payload)
    r2 = await async_client.post("/api/v1/auth/request-otp", json=payload)
    r3 = await async_client.post("/api/v1/auth/request-otp", json=payload)

    assert r1.status_code in {200, 400, 409}
    assert r2.status_code in {200, 400, 409}
    assert r3.status_code == 429
    assert r3.json().get("detail") == "otp_phone_rate_limited"


@pytest.mark.asyncio
async def test_login_identifier_rate_limit(async_client, monkeypatch):
    monkeypatch.setenv("LOGIN_IDENTIFIER_RATE_LIMIT", "2")
    monkeypatch.setenv("LOGIN_IDENTIFIER_RATE_WINDOW", "60")
    monkeypatch.setattr(deps, "_RATE_ENABLED", True)
    monkeypatch.setattr(deps, "_rate_limiter", RateLimiter(redis=None, env="test", prefix="rl"))

    payload = {"identifier": "user@example.com", "password": "bad"}

    r1 = await async_client.post("/api/v1/auth/login", json=payload)
    r2 = await async_client.post("/api/v1/auth/login", json=payload)
    r3 = await async_client.post("/api/v1/auth/login", json=payload)

    assert r1.status_code in {401, 403, 422}
    assert r2.status_code in {401, 403, 422}
    assert r3.status_code == 429
    assert r3.json().get("detail") == "login_rate_limited"
