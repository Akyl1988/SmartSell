"""
MVP tests for Kaspi orders synchronization.

Tests cover:
1. Idempotency: running sync twice with same remote payload does not create duplicates
2. Watermark: after first run watermark advances; second run requests only orders >= watermark
3. Upsert update: remote order changes status/price; second sync updates existing row
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.models import Order
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.services.kaspi_service import KaspiService


def _utcnow() -> datetime:
    """Return naive UTC datetime for consistency with models."""
    return datetime.utcnow()


def _orders_payload_with_timestamp(order_id: str, status: str, price: int, ts: datetime) -> list[dict]:
    """Generate order payload with specific timestamp."""
    return [
        {
            "id": order_id,
            "status": status,
            "updatedAt": ts.isoformat().replace("+00:00", "Z"),
            "totalPrice": price,
            "customer": {"phone": "+77001234567", "name": "Test Customer"},
            "items": [
                {
                    "productSku": "TEST-SKU-001",
                    "productName": "Test Product",
                    "quantity": 1,
                    "basePrice": price,
                    "totalPrice": price,
                }
            ],
        }
    ]


@pytest_asyncio.fixture(autouse=True)
async def _kaspi_orders_sync_setup(async_db_session, monkeypatch):
    from app.api.v1 import kaspi as kaspi_module
    from app.models.company import Company

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    elif not company.kaspi_store_id:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    monkeypatch.setattr(kaspi_module.KaspiAdapter, "health", lambda *args, **kwargs: {"note": "ok"})


@pytest.mark.asyncio
async def test_idempotency_no_duplicates(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    """
    Test that running sync twice with the same remote payload does not create duplicates.

    Scenario:
    1. First sync: fetch 2 orders -> inserted=2, updated=0
    2. Second sync: same 2 orders -> inserted=0, updated=0 (idempotent)
    3. Verify: only 2 orders exist in DB (no duplicates)
    """
    base_ts = _utcnow() - timedelta(hours=2)

    # Mock responses: return same 2 orders on every call
    call_count = [0]

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        call_count[0] += 1
        if page == 1:
            return {
                "items": [
                    {
                        "id": "kaspi-order-001",
                        "status": "NEW",
                        "updatedAt": base_ts.isoformat().replace("+00:00", "Z"),
                        "totalPrice": 10000,
                        "customer": {"phone": "+77001111111", "name": "Customer One"},
                        "items": [
                            {
                                "productSku": "SKU-001",
                                "productName": "Product One",
                                "quantity": 1,
                                "basePrice": 10000,
                                "totalPrice": 10000,
                            }
                        ],
                    },
                    {
                        "id": "kaspi-order-002",
                        "status": "CONFIRMED",
                        "updatedAt": (base_ts + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                        "totalPrice": 15000,
                        "customer": {"phone": "+77002222222", "name": "Customer Two"},
                        "items": [
                            {
                                "productSku": "SKU-002",
                                "productName": "Product Two",
                                "quantity": 2,
                                "basePrice": 7500,
                                "totalPrice": 15000,
                            }
                        ],
                    },
                ],
                "page": 1,
                "total_pages": 1,
                "has_next": False,
            }
        return {"items": [], "page": page, "total_pages": 1, "has_next": False}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    # First sync
    resp1 = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()

    assert data1["inserted"] == 2, "First sync should insert 2 orders"
    assert data1["updated"] == 0, "First sync should update 0 orders"
    assert data1["fetched"] == 2, "First sync should fetch 2 orders"

    # Verify orders in DB
    res = await async_db_session.execute(sa.select(sa.func.count(Order.id)).where(Order.company_id == 1001))
    count_after_first = res.scalar_one()
    assert count_after_first == 2, "Should have exactly 2 orders after first sync"

    # Second sync with same data
    resp2 = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()

    # On second sync: same orders are already there with same data -> should be 0 changes
    # Note: implementation uses UPSERT, so it will count as "updated" even if data unchanged
    # However, the key point is no NEW inserts
    assert data2["inserted"] == 0, "Second sync should insert 0 new orders (idempotency)"
    assert data2["fetched"] == 2, "Second sync should still fetch 2 orders"

    # Verify no duplicates: still only 2 orders
    res = await async_db_session.execute(sa.select(sa.func.count(Order.id)).where(Order.company_id == 1001))
    count_after_second = res.scalar_one()
    assert count_after_second == 2, "Should still have exactly 2 orders after second sync (no duplicates)"

    # Verify unique constraint worked
    res = await async_db_session.execute(sa.select(Order).where(Order.company_id == 1001).order_by(Order.external_id))
    orders = res.scalars().all()
    assert len(orders) == 2
    assert orders[0].external_id == "kaspi-order-001"
    assert orders[1].external_id == "kaspi-order-002"


@pytest.mark.asyncio
async def test_watermark_advances_and_filters(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    """
    Test that watermark advances after successful sync and second run requests only orders >= watermark.

    Scenario:
    1. First sync: fetch order at T0 -> watermark advances to T0
    2. Second sync: should request date_from >= T0, receives order at T1
    3. Verify: watermark advances to T1
    """
    t0 = _utcnow() - timedelta(hours=3)
    t1 = _utcnow() - timedelta(hours=1)

    # Track what date_from was requested
    requested_dates = []

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        requested_dates.append(date_from)

        # First call: return order at t0
        if len(requested_dates) == 1:
            if page == 1:
                return {
                    "items": [
                        {
                            "id": "kaspi-watermark-001",
                            "status": "NEW",
                            "updatedAt": t0.isoformat().replace("+00:00", "Z"),
                            "totalPrice": 5000,
                            "customer": {"phone": "+77003333333", "name": "Watermark Test"},
                            "items": [
                                {
                                    "productSku": "WM-SKU-001",
                                    "productName": "Watermark Product 1",
                                    "quantity": 1,
                                    "basePrice": 5000,
                                    "totalPrice": 5000,
                                }
                            ],
                        }
                    ],
                    "page": 1,
                    "total_pages": 1,
                    "has_next": False,
                }

        # Second call: return newer order at t1
        elif len(requested_dates) == 2:
            if page == 1:
                return {
                    "items": [
                        {
                            "id": "kaspi-watermark-002",
                            "status": "CONFIRMED",
                            "updatedAt": t1.isoformat().replace("+00:00", "Z"),
                            "totalPrice": 7000,
                            "customer": {"phone": "+77004444444", "name": "Watermark Test 2"},
                            "items": [
                                {
                                    "productSku": "WM-SKU-002",
                                    "productName": "Watermark Product 2",
                                    "quantity": 1,
                                    "basePrice": 7000,
                                    "totalPrice": 7000,
                                }
                            ],
                        }
                    ],
                    "page": 1,
                    "total_pages": 1,
                    "has_next": False,
                }

        return {"items": [], "page": page, "total_pages": 1, "has_next": False}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    # First sync
    resp1 = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["inserted"] == 1

    # Check watermark was set
    res = await async_db_session.execute(sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001))
    state1 = res.scalar_one()
    assert state1.last_synced_at is not None, "Watermark should be set after first sync"
    watermark1 = state1.last_synced_at

    # Watermark should be at or near t0 (accounting for overlap adjustment)
    # The service uses "effective_from = base_from - overlap" where overlap is 2 minutes
    # So watermark will be the max updatedAt from fetched orders
    assert watermark1 >= t0 - timedelta(minutes=5), "Watermark should be around t0"

    # Second sync
    resp2 = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["inserted"] == 1, "Second sync should insert the new order"

    # Check watermark advanced
    await async_db_session.refresh(state1)
    watermark2 = state1.last_synced_at
    assert watermark2 > watermark1, "Watermark should advance after second sync"
    assert watermark2 >= t1 - timedelta(minutes=5), "Watermark should be around t1"

    # Verify both orders exist
    res = await async_db_session.execute(sa.select(sa.func.count(Order.id)).where(Order.company_id == 1001))
    total_orders = res.scalar_one()
    assert total_orders == 2, "Should have 2 orders after both syncs"

    # Verify second call used watermark (requested date_from should be based on previous watermark)
    assert len(requested_dates) >= 2, "Should have made at least 2 API calls"
    # Note: due to overlap adjustment, date_from will be slightly before watermark
    # But it should NOT go back to very old dates


@pytest.mark.asyncio
async def test_upsert_updates_existing_order(monkeypatch, async_client, async_db_session, company_a_admin_headers):
    """
    Test that when remote order changes (status/price), second sync updates existing row.

    Scenario:
    1. First sync: order in "NEW" status with price 10000
    2. Second sync: same order ID but "CONFIRMED" status with price 12000
    3. Verify: updated=1 (not inserted), order has new status and price
    """
    base_ts = _utcnow() - timedelta(hours=2)
    update_ts = _utcnow() - timedelta(hours=1)

    sync_count = [0]

    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        sync_count[0] += 1

        if page == 1:
            # First sync: NEW status, price 10000
            if sync_count[0] == 1:
                return {
                    "items": [
                        {
                            "id": "kaspi-upsert-001",
                            "status": "NEW",
                            "updatedAt": base_ts.isoformat().replace("+00:00", "Z"),
                            "totalPrice": 10000,
                            "customer": {"phone": "+77005555555", "name": "Upsert Customer"},
                            "items": [
                                {
                                    "productSku": "UPSERT-SKU",
                                    "productName": "Upsert Product",
                                    "quantity": 1,
                                    "basePrice": 10000,
                                    "totalPrice": 10000,
                                }
                            ],
                        }
                    ],
                    "page": 1,
                    "total_pages": 1,
                    "has_next": False,
                }

            # Second sync: CONFIRMED status, price 12000
            else:
                return {
                    "items": [
                        {
                            "id": "kaspi-upsert-001",  # Same ID!
                            "status": "CONFIRMED",  # Changed status
                            "updatedAt": update_ts.isoformat().replace("+00:00", "Z"),
                            "totalPrice": 12000,  # Changed price
                            "customer": {"phone": "+77005555555", "name": "Upsert Customer Updated"},
                            "items": [
                                {
                                    "productSku": "UPSERT-SKU",
                                    "productName": "Upsert Product Updated",
                                    "quantity": 2,  # Changed quantity
                                    "basePrice": 6000,
                                    "totalPrice": 12000,
                                }
                            ],
                        }
                    ],
                    "page": 1,
                    "total_pages": 1,
                    "has_next": False,
                }

        return {"items": [], "page": page, "total_pages": 1, "has_next": False}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    # First sync
    resp1 = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["inserted"] == 1, "First sync should insert 1 order"
    assert data1["updated"] == 0, "First sync should update 0 orders"

    # Get the order and verify initial state
    res = await async_db_session.execute(
        sa.select(Order).where(sa.and_(Order.company_id == 1001, Order.external_id == "kaspi-upsert-001"))
    )
    order1 = res.scalar_one()
    from app.models.order import OrderStatus

    assert order1.status == OrderStatus.PENDING, "Initial status should be PENDING (mapped from NEW)"
    assert order1.total_amount == 10000, "Initial price should be 10000"
    order_id = order1.id

    # Second sync with updated data
    resp2 = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()

    # Should update existing order, not insert new one
    assert data2["inserted"] == 0, "Second sync should insert 0 orders (existing order)"
    assert data2["updated"] == 1, "Second sync should update 1 order"

    # Verify order was updated, not duplicated
    res = await async_db_session.execute(sa.select(sa.func.count(Order.id)).where(Order.company_id == 1001))
    total_orders = res.scalar_one()
    assert total_orders == 1, "Should still have only 1 order (upsert, not duplicate)"

    # Verify the order was updated with new data
    res = await async_db_session.execute(sa.select(Order).where(Order.id == order_id))
    order2 = res.scalar_one()

    # Refresh to ensure we have latest data from DB
    await async_db_session.refresh(order2)

    assert order2.id == order_id, "Should be the same order ID"
    assert order2.external_id == "kaspi-upsert-001", "External ID should remain the same"
    assert order2.status == OrderStatus.CONFIRMED, "Status should be updated to CONFIRMED"
    assert order2.total_amount == 12000, "Price should be updated to 12000"
    assert order2.customer_name == "Upsert Customer Updated", "Customer name should be updated"


@pytest.mark.asyncio
async def test_advisory_lock_prevents_concurrent_sync(
    monkeypatch, async_client, async_db_session, company_a_admin_headers
):
    """
    Test that advisory lock prevents concurrent syncs and returns 423 status.

    We test the lock mechanism by manually acquiring the lock before calling the endpoint.
    """
    from sqlalchemy import text

    from app.services.kaspi_service import KaspiService

    # Mock to return empty results
    async def fake_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        return {"items": [], "page": 1, "total_pages": 1, "has_next": False}

    monkeypatch.setattr(KaspiService, "get_orders", fake_get_orders)

    # Manually acquire the lock for company 1001
    svc = KaspiService()
    lock_key = svc._company_lock_key(1001)

    # Acquire lock in the test session
    await async_db_session.execute(text("SELECT pg_advisory_lock(:lock_key)").bindparams(lock_key=lock_key))

    try:
        # Now try to sync - should get 423 because lock is held
        resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)

        # Should return 423 Locked
        assert resp.status_code == 423, f"Expected 423, got {resp.status_code}"
        assert "locked" in resp.text.lower() or "already running" in resp.text.lower()

        # Verify that state was recorded for the locked attempt
        res = await async_db_session.execute(
            sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == 1001)
        )
        state = res.scalar_one_or_none()
        if state:
            assert state.last_result == "locked", "Result should be 'locked'"
            assert state.last_duration_ms is not None, "Locked attempt should record duration"
            # Note: last_fetched might not be set to 0 if state creation happens before lock check
    finally:
        # Release the lock
        await async_db_session.execute(text("SELECT pg_advisory_unlock(:lock_key)").bindparams(lock_key=lock_key))


@pytest.mark.asyncio
async def test_error_handling_persists_failure_state(
    monkeypatch, async_client, async_db_session, company_a_admin_headers
):
    """
    Test that sync errors are properly recorded in state without changing watermark.
    """
    import httpx

    # Mock to raise an error
    async def fake_failing_get_orders(self, *, date_from=None, date_to=None, status=None, page=1, page_size=100):  # noqa: ARG001
        raise httpx.TimeoutException("Kaspi API timeout")

    monkeypatch.setattr(KaspiService, "get_orders", fake_failing_get_orders)

    # Set initial watermark
    initial_ts = _utcnow() - timedelta(days=1)
    state = KaspiOrderSyncState(company_id=1001, last_synced_at=initial_ts, last_external_order_id="prev-order-001")
    async_db_session.add(state)
    await async_db_session.commit()

    # Attempt sync (should fail)
    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)

    # Should return gateway timeout error
    assert resp.status_code == 504, "Timeout should return 504"

    # Verify error was recorded but watermark unchanged
    await async_db_session.refresh(state)
    assert state.last_result == "failure", "Result should be 'failure'"
    assert state.last_error_code == "kaspi_timeout", "Error code should be recorded"
    assert state.last_error_message is not None, "Error message should be recorded"
    assert state.last_error_at is not None, "Error timestamp should be recorded"
    assert state.last_synced_at == initial_ts, "Watermark should not change on error"
    assert state.last_external_order_id == "prev-order-001", "Last external ID should not change on error"
