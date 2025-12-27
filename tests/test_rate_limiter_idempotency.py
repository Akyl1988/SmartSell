from __future__ import annotations

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.exceptions import RateLimitError
from app.core.idempotency import IdempotencyEnforcer, ensure_idempotency_dep
from app.core.rate_limiter import RateLimiter, rate_limit_dependency


def _build_rate_limited_app():
    limiter = RateLimiter(redis=None, env="test")
    dep = rate_limit_dependency(limiter, tag="auth", max_requests=2, window_seconds=60)

    app = FastAPI()

    @app.exception_handler(RateLimitError)
    async def _handle_rl(request, exc):  # pragma: no cover - simple mapping
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": str(exc)},
            headers=getattr(exc, "headers", None) or {},
        )

    @app.get("/limited", dependencies=[Depends(dep)])
    async def limited():
        return {"ok": True}

    return app


def _build_idempotent_app():
    idem = IdempotencyEnforcer(redis=None, prefix="idemp-test", default_ttl=30, env="test")
    dep = ensure_idempotency_dep(idem)

    app = FastAPI()

    @app.post("/idem", dependencies=[Depends(dep)])
    async def idem_endpoint(request: Request):
        key = getattr(getattr(request, "state", None), "idempotency_key", None)
        if key:
            await idem.set_result(key, status_code=200)
        return {"ok": True}

    return app


def test_rate_limiter_falls_back_to_memory_and_blocks_after_limit():
    app = _build_rate_limited_app()
    client = TestClient(app)

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200
    resp = client.get("/limited")
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After")


def test_idempotency_blocks_duplicate_keys_in_memory():
    app = _build_idempotent_app()
    client = TestClient(app)

    headers = {"Idempotency-Key": "demo-key"}

    first = client.post("/idem", headers=headers)
    assert first.status_code == 200

    duplicate = client.post("/idem", headers=headers)
    assert duplicate.status_code == 409

    other = client.post("/idem", headers={"Idempotency-Key": "demo-key-2"})
    assert other.status_code == 200
