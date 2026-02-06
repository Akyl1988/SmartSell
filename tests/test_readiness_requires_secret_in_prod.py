from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_readiness_requires_secret_in_prod(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setenv("READINESS_STRICT", "1")
    monkeypatch.setenv("READINESS_REQUIRE_SECRET", "1")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.delenv("APP_SECRET", raising=False)

    resp = await client.get("/ready")
    assert resp.status_code == 503, resp.text
    payload = resp.json()
    assert payload.get("secrets", {}).get("ok") is False
