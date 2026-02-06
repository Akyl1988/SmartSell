from __future__ import annotations

import pytest

from app.core.config import settings
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.providers.mobizon import otp as mobizon_mod
from app.integrations.providers.mobizon.otp import MobizonOtpProvider


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}"

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def request(self, *_args, **_kwargs):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.asyncio
async def test_mobizon_send_otp_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    resp = _FakeResponse(200, {"code": 0, "data": {"messageId": "m1"}})
    monkeypatch.setattr(mobizon_mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    provider = MobizonOtpProvider(config={"api_key": "k", "sender": "S"})
    result = await provider.send_otp("+77001234567", "1234", 60, metadata={"text": "code 1234"})

    assert result.get("status") == "ok"
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_mobizon_send_otp_unauthorized(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    resp = _FakeResponse(401, {"error": "unauthorized"})
    monkeypatch.setattr(mobizon_mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    provider = MobizonOtpProvider(config={"api_key": "k", "sender": "S"})
    with pytest.raises(ProviderNotConfiguredError, match="otp_provider_auth_failed"):
        await provider.send_otp("+77001234567", "1234", 60, metadata={"text": "code 1234"})


@pytest.mark.asyncio
async def test_mobizon_send_otp_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    exc = mobizon_mod.httpx.TimeoutException("timeout")
    monkeypatch.setattr(mobizon_mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(exc))

    provider = MobizonOtpProvider(config={"api_key": "k", "sender": "S"})
    with pytest.raises(ProviderNotConfiguredError, match="otp_provider_unavailable"):
        await provider.send_otp("+77001234567", "1234", 60, metadata={"text": "code 1234"})
