from __future__ import annotations

from datetime import datetime

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
async def test_get_orders_uses_shop_api_v2_and_jsonapi_pagination(monkeypatch):
    svc = KaspiService(api_key="token", base_url="https://kaspi.kz")
    captured: dict[str, object] = {}

    class _DummyResponse:
        status_code = 200
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"items": [], "page": 1, "has_next": False, "total_pages": 1}

    class _DummyClient:
        def __init__(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["params"] = params
            return _DummyResponse()

    monkeypatch.setattr(kaspi_service.settings, "KASPI_SHOP_API_URL", "https://kaspi.kz/shop/api")
    monkeypatch.setattr(KaspiService, "_client", lambda self, **kwargs: _DummyClient())

    await svc.get_orders(
        date_from=datetime(2024, 1, 1),
        date_to=datetime(2024, 1, 2),
        page=2,
        page_size=10,
        merchant_uid="m-1",
    )

    assert captured["url"] == "https://kaspi.kz/shop/api/v2/orders"
    params = captured["params"]
    assert params["page[number]"] == 2
    assert params["page[size]"] == 10
    assert "filter[orders][creationDate][$ge]" in params
    assert "filter[orders][creationDate][$le]" in params
