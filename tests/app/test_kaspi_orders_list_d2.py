from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.kaspi_offer import KaspiOffer
from app.models.order import Order, OrderItem, OrderSource, OrderStatus

pytestmark = pytest.mark.asyncio


async def _create_offer(session, *, company_id: int, merchant_uid: str, sku: str) -> None:
    offer = KaspiOffer(company_id=company_id, merchant_uid=merchant_uid, sku=sku, title="Item", price=1000)
    session.add(offer)
    await session.commit()


def _make_order(
    *,
    company_id: int,
    ext_id: str,
    created_at: datetime,
    status: OrderStatus,
    delivery_date: str | None = None,
    internal_notes: str | None = None,
) -> Order:
    return Order(
        company_id=company_id,
        order_number=f"KASPI-{company_id}-{ext_id}",
        external_id=ext_id,
        source=OrderSource.KASPI,
        status=status,
        customer_name="Alice",
        customer_phone="+70000000000",
        total_amount=Decimal("1000.00"),
        currency="KZT",
        created_at=created_at.replace(tzinfo=None),
        updated_at=created_at.replace(tzinfo=None),
        delivery_date=delivery_date,
        internal_notes=internal_notes,
    )


async def test_kaspi_orders_list_requires_auth(async_client):
    resp = await async_client.get("/api/v1/kaspi/orders?merchantUid=123")
    assert resp.status_code == 401


async def test_kaspi_orders_list_non_admin_forbidden(async_client, company_a_manager_headers):
    resp = await async_client.get("/api/v1/kaspi/orders?merchantUid=123", headers=company_a_manager_headers)
    assert resp.status_code == 403


async def test_kaspi_orders_list_tenant_isolation(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=2001, merchant_uid="999", sku="SKU-ISO")
    order = _make_order(company_id=2001, ext_id="o1", created_at=datetime.now(UTC), status=OrderStatus.PENDING)
    async_db_session.add(order)
    await async_db_session.flush()
    async_db_session.add(
        OrderItem(
            order_id=order.id,
            sku="SKU-ISO",
            name="Item",
            quantity=1,
            unit_price=Decimal("1000.00"),
            total_price=Decimal("1000.00"),
            cost_price=Decimal("100.00"),
        )
    )
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=999",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 404
    data = resp.json()
    assert data["code"] == "merchant_not_found"


async def test_kaspi_orders_list_pagination(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-PAGE")
    now = datetime.utcnow()
    notes = json.dumps(
        {
            "kaspi": {
                "preOrder": True,
                "plannedDeliveryDate": "2026-02-12T00:00:00Z",
                "reservationDate": "2026-02-11T00:00:00Z",
            }
        }
    )
    orders = [
        _make_order(company_id=1001, ext_id="o1", created_at=now - timedelta(days=1), status=OrderStatus.PENDING),
        _make_order(company_id=1001, ext_id="o2", created_at=now - timedelta(hours=12), status=OrderStatus.PENDING),
        _make_order(
            company_id=1001,
            ext_id="o3",
            created_at=now - timedelta(hours=1),
            status=OrderStatus.PENDING,
            delivery_date="2026-02-10T09:00:00Z",
            internal_notes=notes,
        ),
    ]
    async_db_session.add_all(
        [
            orders[0],
            orders[1],
            orders[2],
        ]
    )
    await async_db_session.flush()
    async_db_session.add_all(
        [
            OrderItem(
                order_id=orders[0].id,
                sku="SKU-PAGE",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
            OrderItem(
                order_id=orders[1].id,
                sku="SKU-PAGE",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
            OrderItem(
                order_id=orders[2].id,
                sku="SKU-PAGE",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
        ]
    )
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=123&page=1&limit=2",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["limit"] == 2
    assert len(data["items"]) == 2
    item = next(entry for entry in data["items"] if entry["external_id"] == "o3")
    assert item["delivery_date"] == "2026-02-10T09:00:00Z"
    assert item["kaspi_preorder"] is True
    assert item["kaspi_planned_delivery_date"] == "2026-02-12T00:00:00Z"
    assert item["kaspi_reservation_date"] == "2026-02-11T00:00:00Z"


async def test_kaspi_orders_list_filters(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-FILTER")
    base = datetime(2026, 2, 1)
    orders = [
        _make_order(company_id=1001, ext_id="a1", created_at=base, status=OrderStatus.PENDING),
        _make_order(company_id=1001, ext_id="a2", created_at=base + timedelta(hours=1), status=OrderStatus.SHIPPED),
    ]
    async_db_session.add_all([orders[0], orders[1]])
    await async_db_session.flush()
    async_db_session.add_all(
        [
            OrderItem(
                order_id=orders[0].id,
                sku="SKU-FILTER",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
            OrderItem(
                order_id=orders[1].id,
                sku="SKU-FILTER",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
        ]
    )
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=123&state=shipped&created_from=2026-02-01T00:30:00Z&created_to=2026-02-01T02:00:00Z",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["external_id"] == "a2"


async def test_kaspi_orders_list_status_filter_enum(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-STATUS")
    now = datetime.utcnow()
    orders = [
        _make_order(company_id=1001, ext_id="s1", created_at=now, status=OrderStatus.PAID),
        _make_order(company_id=1001, ext_id="s2", created_at=now, status=OrderStatus.PENDING),
    ]
    async_db_session.add_all(orders)
    await async_db_session.flush()
    async_db_session.add_all(
        [
            OrderItem(
                order_id=orders[0].id,
                sku="SKU-STATUS",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
            OrderItem(
                order_id=orders[1].id,
                sku="SKU-STATUS",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
        ]
    )
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=123&status=OrderStatus.PAID",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["external_id"] == "s1"


async def test_kaspi_orders_list_invalid_status(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/kaspi/orders?status=bad_status",
        headers={**company_a_admin_headers, "X-Request-ID": "req-invalid-status"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "invalid_status"
    assert resp.headers.get("X-Request-ID") == "req-invalid-status"


async def test_kaspi_orders_list_invalid_datetime(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/kaspi/orders?created_from=not-a-date",
        headers={**company_a_admin_headers, "X-Request-ID": "req-invalid-datetime"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "invalid_datetime"
    assert resp.headers.get("X-Request-ID") == "req-invalid-datetime"


async def test_kaspi_orders_list_tenant_isolation_list(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-ISO-A")
    await _create_offer(async_db_session, company_id=2001, merchant_uid="123", sku="SKU-ISO-B")
    order_a = _make_order(company_id=1001, ext_id="ia", created_at=datetime.utcnow(), status=OrderStatus.PAID)
    order_b = _make_order(company_id=2001, ext_id="ib", created_at=datetime.utcnow(), status=OrderStatus.PAID)
    async_db_session.add_all([order_a, order_b])
    await async_db_session.flush()
    async_db_session.add_all(
        [
            OrderItem(
                order_id=order_a.id,
                sku="SKU-ISO-A",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
            OrderItem(
                order_id=order_b.id,
                sku="SKU-ISO-B",
                name="Item",
                quantity=1,
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                cost_price=Decimal("100.00"),
            ),
        ]
    )
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/kaspi/orders?merchantUid=123",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["external_id"] == "ia"


async def test_kaspi_orders_detail_includes_items(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-DETAIL")
    notes = json.dumps(
        {
            "kaspi": {
                "preOrder": False,
                "plannedDeliveryDate": "2026-03-01T00:00:00Z",
                "reservationDate": "2026-02-28T00:00:00Z",
            }
        }
    )
    order = _make_order(
        company_id=1001,
        ext_id="d1",
        created_at=datetime.utcnow(),
        status=OrderStatus.PAID,
        delivery_date="2026-02-27T12:00:00Z",
        internal_notes=notes,
    )
    async_db_session.add(order)
    await async_db_session.flush()
    item = OrderItem(
        order_id=order.id,
        sku="SKU-1",
        name="Item 1",
        quantity=2,
        unit_price=Decimal("500.00"),
        total_price=Decimal("1000.00"),
        cost_price=Decimal("100.00"),
    )
    async_db_session.add(item)
    await async_db_session.commit()

    resp = await async_client.get(f"/api/v1/kaspi/orders/{order.id}", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == order.id
    assert data["items"][0]["sku"] == "SKU-1"
    assert data["delivery_date"] == "2026-02-27T12:00:00Z"
    assert data["kaspi_preorder"] is False
    assert data["kaspi_planned_delivery_date"] == "2026-03-01T00:00:00Z"
    assert data["kaspi_reservation_date"] == "2026-02-28T00:00:00Z"


async def test_kaspi_orders_detail_tenant_isolation(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=2001, merchant_uid="999", sku="SKU-OTHER")
    order = _make_order(company_id=2001, ext_id="d2", created_at=datetime.utcnow(), status=OrderStatus.PAID)
    async_db_session.add(order)
    await async_db_session.flush()
    async_db_session.add(
        OrderItem(
            order_id=order.id,
            sku="SKU-OTHER",
            name="Item",
            quantity=1,
            unit_price=Decimal("1000.00"),
            total_price=Decimal("1000.00"),
            cost_price=Decimal("100.00"),
        )
    )
    await async_db_session.commit()

    resp = await async_client.get(f"/api/v1/kaspi/orders/{order.id}", headers=company_a_admin_headers)
    assert resp.status_code == 404
    data = resp.json()
    assert data["code"] == "order_not_found"
