from __future__ import annotations

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


def _make_order(*, company_id: int, ext_id: str, created_at: datetime, status: OrderStatus) -> Order:
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
    async_db_session.add_all(
        [
            _make_order(company_id=1001, ext_id="o1", created_at=now - timedelta(days=1), status=OrderStatus.PENDING),
            _make_order(company_id=1001, ext_id="o2", created_at=now - timedelta(hours=12), status=OrderStatus.PENDING),
            _make_order(company_id=1001, ext_id="o3", created_at=now - timedelta(hours=1), status=OrderStatus.PENDING),
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


async def test_kaspi_orders_list_filters(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-FILTER")
    base = datetime(2026, 2, 1)
    async_db_session.add_all(
        [
            _make_order(company_id=1001, ext_id="a1", created_at=base, status=OrderStatus.PENDING),
            _make_order(company_id=1001, ext_id="a2", created_at=base + timedelta(hours=1), status=OrderStatus.SHIPPED),
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


async def test_kaspi_orders_detail_includes_items(async_client, async_db_session, company_a_admin_headers):
    await _create_offer(async_db_session, company_id=1001, merchant_uid="123", sku="SKU-DETAIL")
    order = _make_order(company_id=1001, ext_id="d1", created_at=datetime.utcnow(), status=OrderStatus.PAID)
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
