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
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            return _DummyResponse()

    def _client_factory(*, timeout):
        return _DummyClient(timeout=timeout)

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
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["params"] = params
            return _DummyResponse()

    class _DummyRetryClient:
        def __init__(self, **kwargs):
            self._client = _DummyClient()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            return await self._client.get(url, headers=headers, params=params)

    monkeypatch.setattr(kaspi_service, "_RetryingAsyncClient", lambda **kwargs: _DummyRetryClient())

    await svc.get_orders(page=0, page_size=0)

    url = captured.get("url")
    params = captured.get("params")
    assert isinstance(url, str)
    assert url.endswith("/v2/orders")
    assert isinstance(params, dict)
    assert params.get("page[number]") == 1
    assert params.get("page[size]") == 100
