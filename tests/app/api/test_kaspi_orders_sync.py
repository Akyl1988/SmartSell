import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models import Order
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.services.kaspi_service import KaspiService, KaspiSyncAlreadyRunning


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
async def test_concurrent_sync_returns_409(monkeypatch, async_client, company_a_admin_headers):
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return _orders_payload() if page == 1 else []

    lock_calls = {"n": 0}

    async def fake_lock(self, db, company_id):  # noqa: ARG002
        lock_calls["n"] += 1
        if lock_calls["n"] == 1:
            await asyncio.sleep(0.05)
            return
        raise KaspiSyncAlreadyRunning("kaspi sync already running")

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)
    monkeypatch.setattr(KaspiService, "_acquire_company_lock", fake_lock)

    first = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert first.status_code == 200, first.text

    second = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert second.status_code == 409
    assert "sync" in (second.json().get("detail", ""))
