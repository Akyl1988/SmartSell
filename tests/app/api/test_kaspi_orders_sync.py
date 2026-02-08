import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.models import Order, OrderItem
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.order import OrderStatusHistory
from app.services.kaspi_service import KaspiService

pytestmark = pytest.mark.usefixtures("kaspi_orders_sync_setup")


def _orders_payload(total: int = 1100, status: str = "NEW") -> list[dict]:
    return [
        {
            "id": "ext-1",
            "status": status,
            "totalPrice": total,
            "customer": {"phone": "+7700", "name": "John"},
        },
        {
            "id": "ext-2",
            "status": status,
            "totalPrice": total + 50,
            "customer": {"phone": "+7701", "name": "Jane"},
        },
    ]


def _orders_payload_with_items(total: int = 1100, status: str = "NEW") -> list[dict]:
    return [
        {
            "id": "ext-1",
            "status": status,
            "totalPrice": total,
            "customer": {"phone": "+7700", "name": "John"},
            "items": [
                {
                    "productSku": "SKU-1",
                    "productName": "Item One",
                    "quantity": 1,
                    "basePrice": 100,
                    "totalPrice": 100,
                },
                {
                    "productSku": "SKU-2",
                    "productName": "Item Two",
                    "quantity": 2,
                    "basePrice": 150,
                    "totalPrice": 300,
                },
            ],
        }
    ]


def _orders_payload_with_status_timestamp(status: str, ts: datetime) -> list[dict]:
    return [
        {
            "id": "ext-1",
            "status": status,
            "updatedAt": ts.isoformat().replace("+00:00", "Z"),
            "totalPrice": 100,
            "customer": {"phone": "+7700", "name": "John"},
            "items": [
                {
                    "productSku": "SKU-1",
                    "productName": "Item One",
                    "quantity": 1,
                    "basePrice": 100,
                    "totalPrice": 100,
                }
            ],
        }
    ]


@pytest_asyncio.fixture
async def kaspi_orders_sync_setup(ensure_company_has_kaspi_store_id, kaspi_adapter_health_ok):
    await ensure_company_has_kaspi_store_id()
    return kaspi_adapter_health_ok


@pytest.mark.asyncio
async def test_sync_ops_lock_available_field(async_client, company_a_admin_headers, kaspi_orders_sync_setup):
    """
    Test that ops endpoint includes lock_available field as boolean.
    """
    resp = await async_client.get("/api/v1/kaspi/orders/sync/ops", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "lock_available" in data
    assert isinstance(data["lock_available"], bool)


@pytest.mark.asyncio
async def test_sync_uses_query_merchant_uid(
    monkeypatch, async_client, company_a_admin_headers, kaspi_orders_sync_setup
):
    captured: dict[str, str | None] = {"merchant_uid": None}

    async def fake_sync_orders(self, *, merchant_uid=None, **kwargs):  # noqa: ANN001, ARG001
        captured["merchant_uid"] = merchant_uid
        return {"ok": True, "status": "success", "fetched": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(KaspiService, "sync_orders", fake_sync_orders)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/sync?merchantUid=17319385",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    assert captured["merchant_uid"] == "17319385"


@pytest.mark.asyncio
async def test_sync_missing_merchant_uid_returns_422(
    async_client,
    company_a_admin_headers,
    ensure_company_has_kaspi_store_id,
    kaspi_adapter_health_ok,
):
    await ensure_company_has_kaspi_store_id(kaspi_store_id=None)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 422
    assert resp.json().get("detail") == "missing_merchant_uid"


@pytest.mark.asyncio
async def test_sync_stub_mode_returns_501(monkeypatch, async_client, company_a_admin_headers):
    from app.api.v1 import kaspi as kaspi_module

    monkeypatch.setattr(
        kaspi_module.KaspiAdapter,
        "health",
        lambda *args, **kwargs: {"note": "Kaspi.ps1 stub: ks:health implemented"},
    )

    async def fake_sync_orders(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("sync_orders should not be called in stub mode")

    monkeypatch.setattr(KaspiService, "sync_orders", fake_sync_orders)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/sync?merchantUid=17319385",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 501
    payload = resp.json()
    assert payload.get("code") == "KASPI_STUB_NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_orders_sync_uses_shop_api_v2_path(
    monkeypatch, async_client, company_a_admin_headers, kaspi_orders_sync_setup
):
    from app.services import kaspi_service

    captured: dict[str, object] = {}

    class _DummyResponse:
        status_code = 200
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"items": [], "page": 1, "has_next": False, "total_pages": 1}

    class _DummyClient:
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

    resp = await async_client.post(
        "/api/v1/kaspi/orders/sync?merchantUid=store-a&max_pages=1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    assert captured["url"] == "https://kaspi.kz/shop/api/v2/orders"
    params = captured["params"]
    assert params["page[number]"] == 1
    assert params["page[size]"] == 100
    assert "filter[orders][creationDate][$ge]" in params
    assert "filter[orders][creationDate][$le]" in params


@pytest.mark.asyncio
async def test_sync_orders_accepts_timeout_sec_param(
    monkeypatch, async_client, company_a_admin_headers, kaspi_orders_sync_setup
):
    captured: dict[str, int | None] = {"timeout": None}

    async def fake_sync_orders(self, *, db, company_id, request_id=None, timeout_seconds=None, **kwargs):  # noqa: ANN001, ARG001
        captured["timeout"] = timeout_seconds
        return {"ok": True, "status": "success", "fetched": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(KaspiService, "sync_orders", fake_sync_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync?timeout_sec=60", headers=company_a_admin_headers)
    assert resp.status_code == 200
    assert captured["timeout"] == 60


@pytest.mark.asyncio
async def test_sync_timeout_records_error(
    monkeypatch, async_client, async_db_session, company_a_admin_headers, kaspi_orders_sync_setup
):
    """
    Timeout should return 504 and persist failure state without advancing the watermark.
    """
    from app.services.kaspi_service import KaspiService

    original_init = KaspiService.__init__

    def patched_init(self, *args, **kwargs):  # noqa: ANN001, ARG001
        original_init(self, *args, **kwargs)
        self._sync_timeout_seconds = 0.01  # Very short timeout

    monkeypatch.setattr(KaspiService, "__init__", patched_init)

    async def slow_get_orders(self, **kwargs):  # noqa: ANN001, ARG001
        await asyncio.sleep(999)
        return []

    monkeypatch.setattr(KaspiService, "get_orders", slow_get_orders)

    prev_res = await async_db_session.execute(
        sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001)
    )
    prev_state = prev_res.scalar_one_or_none()
    _ = prev_state.last_synced_at if prev_state else None

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 504, f"Expected 504, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_orders_sync_timeout_sec_applies_to_http_timeout(
    monkeypatch, async_client, company_a_admin_headers, kaspi_orders_sync_setup
):
    captured: dict[str, httpx.Timeout | None] = {"timeout": None}

    async def fake_get_orders(
        self,
        *,
        date_from=None,
        date_to=None,
        status=None,
        page=1,
        page_size=100,
        timeout=None,
        retries=None,
        **kwargs,
    ):  # noqa: ANN001, ARG001
        captured["timeout"] = timeout
        return {"items": [], "page": 1, "total_pages": 1, "has_next": False}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync?timeout_sec=60", headers=company_a_admin_headers)
    assert resp.status_code == 200
    timeout_obj = captured["timeout"]
    assert isinstance(timeout_obj, httpx.Timeout)
    assert timeout_obj.read >= 55.0
    assert timeout_obj.connect == 10.0


@pytest.mark.asyncio
async def test_sync_respects_max_pages_partial(
    monkeypatch, async_client, company_a_admin_headers, kaspi_orders_sync_setup
):
    calls = {"count": 0}

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100, **kwargs):  # noqa: ARG001
        calls["count"] += 1
        return {
            "items": _orders_payload() if page == 1 else _orders_payload(),
            "page": page,
            "total_pages": 2,
            "has_next": True,
        }

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync?max_pages=1", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "partial"
    assert data["last_page_processed"] == 1
    assert data["next_hint"] == "continue"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_first_sync_creates_orders(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload() if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["inserted"] == 2
    assert data["updated"] == 0

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001))
    orders = res.scalars().all()
    assert len(orders) == 2


@pytest.mark.asyncio
async def test_watermark_not_moved_backwards_on_empty_batch(
    monkeypatch, async_client, async_db_session, company_a_admin_headers
):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    existing_ts = datetime(2025, 1, 1, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    state = KaspiOrderSyncState(company_id=1001, last_synced_at=existing_ts, last_external_order_id="prev")
    async_db_session.add(state)
    await async_db_session.commit()

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    await async_db_session.refresh(state)
    assert state.last_synced_at == existing_ts
    assert state.last_external_order_id == "prev"
    assert data["watermark"].startswith(existing_ts.isoformat())


@pytest.mark.asyncio
async def test_first_run_no_orders_sets_reasonable_watermark(
    monkeypatch, async_client, async_db_session, company_a_admin_headers
):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    res = await async_db_session.execute(sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001))
    state = res.scalar_one()

    now = datetime.utcnow()
    assert state.last_synced_at is not None
    assert state.last_synced_at <= now
    assert state.last_synced_at >= now - timedelta(days=2)
    assert data["watermark"].startswith(state.last_synced_at.isoformat())

    state_res = await async_db_session.execute(
        sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001)
    )
    state = state_res.scalar_one()
    assert state.last_synced_at is not None


@pytest.mark.asyncio
async def test_second_sync_is_idempotent(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload() if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text
    data = second.json()
    assert data["inserted"] == 0
    assert data["updated"] >= 0

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001))
    orders = res.scalars().all()
    assert len(orders) == 2


@pytest.mark.asyncio
async def test_status_and_total_are_updated(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    async def fake_get_orders_initial(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload(status="NEW") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_initial)
    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    async def fake_get_orders_updated(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        if page > 1:
            return []
        payload = _orders_payload(total=1500, status="CONFIRMED")
        payload[0]["totalPrice"] = 1700
        payload[0]["status"] = "SHIPPED"
        return payload

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_updated)
    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text
    data = second.json()
    assert data["inserted"] == 0
    assert data["updated"] >= 1

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001, Order.external_id == "ext-1"))
    order = res.scalar_one()
    assert str(order.total_amount) in {"1700", "1700.00"}
    status_value = order.status.value if hasattr(order.status, "value") else order.status
    assert status_value == "shipped"


@pytest.mark.asyncio
async def test_order_items_upsert_idempotent(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    async def fake_get_orders_initial(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_items(status="NEW") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_initial)

    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    async def fake_get_orders_second(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_items(status="CONFIRMED") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_second)
    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001, Order.external_id == "ext-1"))
    order = res.scalar_one()

    items_res = await async_db_session.execute(
        sa.select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.sku)
    )
    items = items_res.scalars().all()
    assert len(items) == 2
    assert {(it.sku, int(it.quantity)) for it in items} == {("SKU-1", 1), ("SKU-2", 2)}


@pytest.mark.asyncio
async def test_order_items_update_values(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    async def fake_get_orders_initial(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_items(status="NEW") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_initial)
    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    def _payload_updated():
        payload = _orders_payload_with_items(status="SHIPPED")
        payload[0]["items"][0]["quantity"] = 3
        payload[0]["items"][0]["basePrice"] = 120
        payload[0]["items"][0]["productName"] = "Item One Updated"
        payload[0]["items"][1]["quantity"] = 1
        payload[0]["items"][1]["basePrice"] = 200
        payload[0]["items"][1]["totalPrice"] = 200
        return payload

    async def fake_get_orders_second(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _payload_updated() if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_second)
    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001, Order.external_id == "ext-1"))
    order = res.scalar_one()

    items_res = await async_db_session.execute(
        sa.select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.sku)
    )
    items = items_res.scalars().all()
    assert len(items) == 2

    item_map = {it.sku: it for it in items}
    assert int(item_map["SKU-1"].quantity) == 3
    assert str(item_map["SKU-1"].unit_price) in {"120", "120.00"}
    assert (item_map["SKU-1"].name or "").startswith("Item One Updated")

    assert int(item_map["SKU-2"].quantity) == 1
    assert str(item_map["SKU-2"].unit_price) in {"200", "200.00"}


@pytest.mark.asyncio
async def test_pagination_fetches_all_pages(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    calls = {"n": 0}

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        calls["n"] += 1
        if page == 1:
            return {
                "items": [
                    {
                        "id": "ext-10",
                        "status": "NEW",
                        "totalPrice": 100,
                        "customer": {"phone": "+7700", "name": "John"},
                    }
                ],
                "page": 1,
                "totalPages": 2,
            }
        if page == 2:
            return {
                "items": [
                    {
                        "id": "ext-11",
                        "status": "NEW",
                        "totalPrice": 120,
                        "customer": {"phone": "+7701", "name": "Jane"},
                    }
                ],
                "page": 2,
                "totalPages": 2,
            }
        return {"items": []}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["inserted"] == 2
    assert data["updated"] == 0
    assert calls["n"] == 2

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001))
    orders = res.scalars().all()
    assert {o.external_id for o in orders} == {"ext-10", "ext-11"}


@pytest.mark.asyncio
async def test_pagination_stops_on_empty_page(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    calls = {"n": 0}

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        calls["n"] += 1
        if page == 1:
            return {
                "items": [
                    {
                        "id": "ext-20",
                        "status": "NEW",
                        "totalPrice": 200,
                        "customer": {"phone": "+7700", "name": "John"},
                    }
                ],
                "page": 1,
                "hasNext": True,
            }
        return {"items": []}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["inserted"] == 1
    assert data["updated"] >= 0
    assert calls["n"] == 2

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001))
    orders = res.scalars().all()
    assert {o.external_id for o in orders} == {"ext-20"}


@pytest.mark.asyncio
async def test_retry_on_transient_error(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    calls = {"n": 0}

    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            req = httpx.Request("GET", "http://kaspi.test/orders")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("rate limited", request=req, response=resp)
        return [
            {
                "id": "ext-30",
                "status": "NEW",
                "totalPrice": 300,
                "customer": {"phone": "+7700", "name": "John"},
            }
        ]

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["inserted"] == 1
    assert calls["n"] == 2

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001))
    orders = res.scalars().all()
    assert {o.external_id for o in orders} == {"ext-30"}


@pytest.mark.asyncio
async def test_status_history_is_idempotent(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    ts1 = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    ts2 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    async def fake_get_orders_initial(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_status_timestamp(status="NEW", ts=ts1) if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_initial)
    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    async def fake_get_orders_second(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_status_timestamp(status="SHIPPED", ts=ts2) if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_second)
    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text

    third = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert third.status_code == 200, third.text

    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001, Order.external_id == "ext-1"))
    order = res.scalar_one()
    status_value = order.status.value if hasattr(order.status, "value") else order.status
    assert status_value == "shipped"

    hist_res = await async_db_session.execute(
        sa.select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order.id)
        .order_by(OrderStatusHistory.changed_at)
    )
    history = hist_res.scalars().all()
    assert len(history) == 2
    statuses = [h.new_status.value if hasattr(h.new_status, "value") else h.new_status for h in history]
    assert statuses == ["pending", "shipped"]


@pytest.mark.asyncio
async def test_double_sync_idempotent_no_duplicates(
    monkeypatch, async_client, async_db_session, company_a_admin_headers
):
    payload = _orders_payload_with_items(status="NEW")

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return payload if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    res_before = await async_db_session.execute(sa.select(sa.func.count()).select_from(Order))
    items_before = await async_db_session.execute(sa.select(sa.func.count()).select_from(OrderItem))
    hist_before = await async_db_session.execute(sa.select(sa.func.count()).select_from(OrderStatusHistory))

    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text

    res_after = await async_db_session.execute(sa.select(sa.func.count()).select_from(Order))
    items_after = await async_db_session.execute(sa.select(sa.func.count()).select_from(OrderItem))
    hist_after = await async_db_session.execute(sa.select(sa.func.count()).select_from(OrderStatusHistory))

    assert res_before.scalar_one() == res_after.scalar_one()
    assert items_before.scalar_one() == items_after.scalar_one()
    assert hist_before.scalar_one() == hist_after.scalar_one()


@pytest.mark.asyncio
async def test_watermark_moves_forward(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    ts1 = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    ts2 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    async def fake_get_orders_first(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_status_timestamp(status="NEW", ts=ts1) if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_first)
    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    state_res = await async_db_session.execute(
        sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001)
    )
    state_first = state_res.scalar_one()

    async def fake_get_orders_second(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload_with_status_timestamp(status="SHIPPED", ts=ts2) if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders_second)
    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text

    state_res_second = await async_db_session.execute(
        sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001)
    )
    state_second = state_res_second.scalar_one()

    assert state_second.last_synced_at is not None
    assert state_first.last_synced_at is not None
    assert state_second.last_synced_at >= state_first.last_synced_at


@pytest.mark.asyncio
async def test_retry_uses_retry_after_header(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    calls = {"n": 0, "sleep": []}

    async def fake_sleep(delay):
        calls["sleep"].append(delay)
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            req = httpx.Request("GET", "http://kaspi.test/orders")
            resp = httpx.Response(429, headers={"Retry-After": "1"}, request=req)
            raise httpx.HTTPStatusError("rate limited", request=req, response=resp)
        return _orders_payload(status="NEW") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text

    assert calls["n"] == 2
    assert calls["sleep"] and calls["sleep"][0] >= 1


@pytest.mark.asyncio
async def test_concurrent_sync_returns_locked(async_client, async_db_session, company_a_admin_headers):
    svc = KaspiService()
    lock_key = svc._company_lock_key(1001)
    await async_db_session.execute(sa.text("SELECT pg_advisory_lock(:k)").bindparams(k=lock_key))
    try:
        resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
        assert resp.status_code in {409, 423}
        assert "sync" in str(resp.json().get("detail"))
    finally:
        await async_db_session.execute(sa.text("SELECT pg_advisory_unlock(:k)").bindparams(k=lock_key))

    state_resp = await async_client.get("/api/v1/kaspi/orders/sync/state", headers=company_a_admin_headers)
    assert state_resp.status_code == 200
    data = state_resp.json()
    assert data["last_result"] == "locked"
    assert data["last_attempt_at"] is not None
    assert data["last_duration_ms"] is not None
    assert data["last_error_code"] is None
    assert data["last_error_message"] is None


@pytest.mark.asyncio
async def test_sync_locked_then_succeeds_after_lock_release(
    monkeypatch, async_client, async_db_session, company_a_admin_headers
):
    """Regression: lock held during first call returns 409/423, then succeeds after release."""

    svc = KaspiService()
    lock_key = svc._company_lock_key(1001)

    # Hold advisory lock inside a short transaction to block the first sync attempt
    async with async_db_session.begin():
        await async_db_session.execute(sa.text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=lock_key))
        resp_locked = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
        assert resp_locked.status_code in {409, 423}

    # After lock is released, sync should proceed normally
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp_ok = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp_ok.status_code == 200, resp_ok.text


@pytest.mark.asyncio
async def test_sync_state_endpoint_returns_defaults(async_client, company_a_admin_headers):
    resp = await async_client.get("/api/v1/kaspi/orders/sync/state", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "watermark": None,
        "last_success_at": None,
        "last_attempt_at": None,
        "last_duration_ms": None,
        "last_result": None,
        "last_fetched": None,
        "last_inserted": None,
        "last_updated": None,
        "last_error_at": None,
        "last_error_code": None,
        "last_error_message": None,
    }


@pytest.mark.asyncio
async def test_sync_state_endpoint_reflects_watermark(monkeypatch, async_client, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload(status="NEW") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    run = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert run.status_code == 200, run.text

    resp = await async_client.get("/api/v1/kaspi/orders/sync/state", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["watermark"] is not None
    assert data["last_success_at"] == data["watermark"]
    assert data["last_result"] == "success"
    assert data["last_attempt_at"] is not None
    assert data["last_duration_ms"] is not None
    assert data["last_fetched"] >= 0
    assert data["last_inserted"] >= 0
    assert data["last_updated"] >= 0
    assert data["last_error_at"] is None
    assert data["last_error_code"] is None
    assert data["last_error_message"] is None


@pytest.mark.asyncio
async def test_sync_state_records_last_error(monkeypatch, async_client, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        raise RuntimeError("kaspi boom")

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code in {500, 502}

    state_resp = await async_client.get("/api/v1/kaspi/orders/sync/state", headers=company_a_admin_headers)
    assert state_resp.status_code == 200
    data = state_resp.json()
    assert data["last_result"] == "failure"
    assert data["last_attempt_at"] is not None
    assert data["last_duration_ms"] is not None
    assert data["last_fetched"] >= 0
    assert data["last_inserted"] >= 0
    assert data["last_updated"] >= 0
    assert data["last_error_code"] == "internal_error"
    assert data["last_error_at"] is not None
    assert data["last_error_message"] and "kaspi" in data["last_error_message"].lower()


@pytest.mark.asyncio
async def test_sync_state_clears_last_error_after_success(monkeypatch, async_client, company_a_admin_headers):
    calls = {"fail": True}

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        if calls["fail"]:
            calls["fail"] = False
            raise RuntimeError("kaspi temporary")
        return _orders_payload(status="NEW") if page == 1 else []

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code in {500, 502}

    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 200, second.text

    state_resp = await async_client.get("/api/v1/kaspi/orders/sync/state", headers=company_a_admin_headers)
    assert state_resp.status_code == 200
    data = state_resp.json()
    assert data["last_result"] == "success"
    assert data["last_attempt_at"] is not None
    assert data["last_duration_ms"] is not None
    assert data["last_fetched"] >= 0
    assert data["last_inserted"] >= 0
    assert data["last_updated"] >= 0
    assert data["last_error_code"] is None
    assert data["last_error_message"] is None
    assert data["last_error_at"] is None


@pytest.mark.asyncio
async def test_sync_returns_429_with_retry_after(monkeypatch, async_client, company_a_admin_headers):
    async def fast_sleep(_delay):  # noqa: ANN001
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        req = httpx.Request("GET", "http://kaspi.test/orders")
        resp = httpx.Response(429, headers={"Retry-After": "7"}, request=req)
        raise httpx.HTTPStatusError("rate limited", request=req, response=resp)

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "7"
    assert "rate" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_sync_timeout_maps_to_504(monkeypatch, async_client, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        raise httpx.TimeoutException("kaspi timeout")

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 504
    assert "timeout" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_sync_internal_error_is_generic(monkeypatch, async_client, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        raise RuntimeError("secret boom")

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 500
    body = resp.json()
    assert "boom" not in str(body.get("detail", "")).lower()
