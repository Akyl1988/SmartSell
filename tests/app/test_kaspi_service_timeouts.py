from __future__ import annotations

import pytest

from app.services import kaspi_service
from app.services.kaspi_service import KaspiService


@pytest.mark.asyncio
async def test_kaspi_orders_timeout_computation():
    svc = KaspiService(api_key="token", base_url="https://kaspi.kz")
    timeout = svc._orders_timeout(2.5)

    assert timeout.connect <= 3.0
    assert timeout.read >= 1.0
    assert timeout.write >= 1.0
    assert timeout.pool >= 1.0
    assert timeout.connect == 2.5
    assert timeout.read == 5.0
    assert timeout.write == 2.5
    assert timeout.pool == 2.5


@pytest.mark.asyncio
async def test_kaspi_list_orders_uses_computed_timeout(monkeypatch):
    svc = KaspiService(api_key="token", base_url="https://kaspi.kz")
    captured: dict[str, object] = {}

    class _DummyResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": []}

    class _DummyClient:
        def __init__(self, *, timeout, transport=None, http2=None, **kwargs):
            captured["timeout"] = timeout
            captured["transport"] = transport
            captured["http2"] = http2

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            return _DummyResponse()

    def _client_factory(*, timeout, trust_env=False, transport=None, http2=None, **kwargs):
        captured["trust_env"] = trust_env
        return _DummyClient(timeout=timeout, transport=transport, http2=http2)

    monkeypatch.setattr(kaspi_service.settings, "KASPI_ORDERS_TRANSPORT", "async")
    monkeypatch.setattr(kaspi_service.httpx, "AsyncClient", _client_factory)

    await svc.list_orders(
        token="t",
        merchant_uid="m",
        state=None,
        date_from_ms=0,
        date_to_ms=1,
        page=1,
        limit=10,
        include_entries=False,
        request_id="req-1",
        timeout_seconds=12.0,
    )

    expected = svc._orders_timeout(12.0)
    actual = captured.get("timeout")
    assert actual is not None
    assert actual.connect == expected.connect
    assert actual.read == expected.read
    assert actual.write == expected.write
    assert actual.pool == expected.pool
    assert captured.get("trust_env") is False
    transport = captured.get("transport")
    if transport is not None:
        assert getattr(transport, "_http2", False) is False


@pytest.mark.asyncio
async def test_kaspi_get_orders_uses_shop_api_url_and_jsonapi_pagination(monkeypatch):
    svc = KaspiService(api_key="token", base_url="https://kaspi.kz")
    captured: dict[str, object] = {}

    class _DummyResponse:
        status_code = 200
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": []}

    class _DummyClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            captured["trust_env"] = kwargs.get("trust_env")
            captured["http2"] = kwargs.get("http2")
            captured["transport"] = kwargs.get("transport")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _DummyResponse()

    monkeypatch.setattr(kaspi_service.settings, "KASPI_ORDERS_TRANSPORT", "async")
    monkeypatch.setattr(kaspi_service.httpx, "AsyncClient", _DummyClient)

    await svc.get_orders(page=0, page_size=0)

    url = captured.get("url")
    params = captured.get("params")
    headers = captured.get("headers")
    assert isinstance(url, str)
    assert url.endswith("/shop/api/v2/orders")
    assert isinstance(params, list)
    assert ("page[number]", 1) in params
    assert ("page[size]", 100) in params
    assert captured.get("trust_env") is False
    transport = captured.get("transport")
    if transport is not None:
        assert getattr(transport, "_http2", False) is False
    assert isinstance(headers, dict)
    assert headers.get("Accept") == "application/vnd.api+json"
    assert headers.get("Content-Type") == "application/vnd.api+json"
    assert headers.get("User-Agent")
    assert headers.get("X-Auth-Token") == "token"


@pytest.mark.asyncio
async def test_kaspi_get_orders_sync_transport_uses_threadpool(monkeypatch):
    svc = KaspiService(api_key="token", base_url="https://kaspi.kz")
    captured: dict[str, object] = {"run_sync": False}

    class _DummyResponse:
        status_code = 200
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": []}

    class _DummyClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            captured["trust_env"] = kwargs.get("trust_env")
            captured["transport"] = kwargs.get("transport")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _DummyResponse()

    async def _run_sync(func, *args, abandon_on_cancel=True, **kwargs):
        captured["run_sync"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr(kaspi_service.settings, "KASPI_ORDERS_TRANSPORT", "sync")
    monkeypatch.setattr(kaspi_service.httpx, "Client", _DummyClient)
    monkeypatch.setattr(kaspi_service.anyio.to_thread, "run_sync", _run_sync)

    await svc.get_orders(page=1, page_size=1)

    assert captured.get("run_sync") is True
    assert captured.get("trust_env") is False
    transport = captured.get("transport")
    if transport is not None:
        assert getattr(transport, "_http2", False) is False
    timeout = captured.get("timeout")
    assert timeout is not None
    assert timeout.read >= 10.0
    headers = captured.get("headers")
    assert isinstance(headers, dict)
    assert headers.get("Accept") == "application/vnd.api+json"
    assert headers.get("Content-Type") == "application/vnd.api+json"
    assert headers.get("User-Agent")
    assert headers.get("X-Auth-Token") == "token"
