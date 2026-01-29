"""Tests for unified error response contract."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.exceptions import register_exception_handlers


class _Payload(BaseModel):
    name: str


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/http-unauthorized")
    async def http_unauthorized() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    @app.post("/validate")
    async def validate(_: _Payload) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    return app


def test_http_exception_contract_preserves_request_id_header() -> None:
    app = _build_app()
    client = TestClient(app)

    resp = client.get("/http-unauthorized", headers={"X-Request-ID": "req-123"})
    data = resp.json()

    assert resp.status_code == 401
    assert data["detail"] == "unauthorized"
    assert data["code"] == "HTTP_401"
    assert data["request_id"] == "req-123"
    assert resp.headers.get("X-Request-ID") == "req-123"


def test_not_found_contract_includes_request_id() -> None:
    app = _build_app()
    client = TestClient(app)

    resp = client.get("/missing")
    data = resp.json()

    assert resp.status_code == 404
    assert data["code"] == "HTTP_404"
    assert data["request_id"]
    assert resp.headers.get("X-Request-ID") == data["request_id"]


def test_request_validation_contract_includes_code() -> None:
    app = _build_app()
    client = TestClient(app)

    resp = client.post("/validate", json={})
    data = resp.json()

    assert resp.status_code == 422
    assert data["code"] == "REQUEST_VALIDATION_ERROR"
    assert data["request_id"]
    assert resp.headers.get("X-Request-ID") == data["request_id"]


def test_unhandled_exception_contract_includes_code() -> None:
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/boom")
    data = resp.json()

    assert resp.status_code == 500
    assert data["code"] == "INTERNAL_ERROR"
    assert data["detail"] == "internal_error"
    assert data["request_id"]
    assert resp.headers.get("X-Request-ID") == data["request_id"]
