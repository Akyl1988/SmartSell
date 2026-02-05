from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from app.core.config import settings
from app.core.idempotency import IdempotencyEnforcer


class FailingRedis:
    async def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_idempotency_redis_down_in_prod_returns_503(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    enforcer = IdempotencyEnforcer(redis=FailingRedis(), env="production")

    app = FastAPI()

    @app.post("/probe", dependencies=[Depends(enforcer.dependency())])
    async def probe():
        return {"ok": True}

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/probe", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 503
    assert resp.json().get("detail") == "idempotency_unavailable"

    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    enforcer_dev = IdempotencyEnforcer(redis=FailingRedis(), env="development")

    app_dev = FastAPI()

    @app_dev.post("/probe", dependencies=[Depends(enforcer_dev.dependency())])
    async def probe_dev():
        return {"ok": True}

    async with AsyncClient(app=app_dev, base_url="http://test") as client:
        resp_dev = await client.post("/probe", headers={"Idempotency-Key": "k1"})

    assert resp_dev.status_code == 200
