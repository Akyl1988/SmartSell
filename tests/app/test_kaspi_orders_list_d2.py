from datetime import UTC, datetime

import pytest

from app.models.kaspi_offer import KaspiOffer
from app.services.kaspi_service import KaspiService

pytestmark = pytest.mark.asyncio


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("bad response")


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, capture: dict):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self._capture["url"] = url
        self._capture["headers"] = headers or {}
        self._capture["params"] = params or {}
        return self._response


async def _create_offer(session, *, company_id: int, merchant_uid: str):
    offer = KaspiOffer(company_id=company_id, merchant_uid=merchant_uid, sku="SKU", title="Item", price=1000)
    session.add(offer)
    await session.commit()


async def test_kaspi_orders_list_requires_auth(async_client):
    resp = await async_client.get("/api/v1/kaspi/orders?merchantUid=123")
    assert resp.status_code in {401, 403}


async def test_kaspi_orders_list_tenant_isolation(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    from app.api.v1 import kaspi as kaspi_module

    await _create_offer(async_db_session, company_id=2001, merchant_uid="999")

    async def _resolve_token(session, company_id: int):
        return "store-a", "token-a"

    monkeypatch.setattr(kaspi_module, "_resolve_kaspi_token", _resolve_token)

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=999",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 404
    data = resp.json()
    assert data["code"] == "merchant_not_found"


async def test_kaspi_orders_list_validation(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    from app.api.v1 import kaspi as kaspi_module

    await _create_offer(async_db_session, company_id=1001, merchant_uid="123")

    async def _resolve_token(session, company_id: int):
        return "store-a", "token-a"

    monkeypatch.setattr(kaspi_module, "_resolve_kaspi_token", _resolve_token)

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=ABC",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 422

    resp2 = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=123&days_back=20",
        headers=company_a_admin_headers,
    )
    assert resp2.status_code == 422


async def test_kaspi_orders_list_happy_path(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    from app.api.v1 import kaspi as kaspi_module

    await _create_offer(async_db_session, company_id=1001, merchant_uid="123")

    async def _resolve_token(session, company_id: int):
        return "store-a", "token-a"

    async def _list_orders(self, **kwargs):
        return {
            "ok": True,
            "data": [
                {
                    "order_id": "o1",
                    "state": "NEW",
                    "creation_date": datetime(2025, 1, 1, tzinfo=UTC),
                    "total_price": 1000,
                    "customer": {"name": "Alice"},
                    "entries": [{"sku": "S1"}],
                }
            ],
        }

    monkeypatch.setattr(kaspi_module, "_resolve_kaspi_token", _resolve_token)
    monkeypatch.setattr(KaspiService, "list_orders", _list_orders)

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=123&state=NEW&page=0&limit=2",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["data"][0]["order_id"] == "o1"


async def test_kaspi_orders_list_service_params(monkeypatch):
    capture: dict = {}
    payload = {
        "data": [
            {
                "id": "o1",
                "attributes": {
                    "state": "NEW",
                    "creationDate": 1700000000000,
                    "totalPrice": 1000,
                },
            }
        ],
    }

    monkeypatch.setattr(
        "app.services.kaspi_service.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(_FakeResponse(200, payload), capture),
    )

    svc = KaspiService()
    out = await svc.list_orders(
        token="token",
        merchant_uid="123",
        state="NEW",
        date_from_ms=1700000000000,
        date_to_ms=1700003600000,
        page=0,
        limit=50,
        include_entries=True,
        request_id="rid",
    )
    assert out["ok"] is True
    assert capture["params"]["filter[orders][merchantUid]"] == "123"
    assert capture["params"]["filter[orders][creationDate][$ge]"] == 1700000000000
    assert capture["params"]["filter[orders][creationDate][$le]"] == 1700003600000
    assert capture["params"]["filter[orders][state]"] == "NEW"
    assert capture["params"]["include[orders]"] == "entries"
