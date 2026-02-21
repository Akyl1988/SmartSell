from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.company import Company
from app.models.order import Order, OrderItem, OrderStatus

pytestmark = pytest.mark.asyncio


def _seed_order(
    *,
    company_id: int,
    order_number: str,
    created_at: datetime,
    total_amount: Decimal,
    currency: str = "KZT",
    external_id: str | None = None,
    items_count: int = 1,
    delivery_date: str | None = None,
    internal_notes: str | None = None,
) -> int:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        company = s.get(Company, company_id)
        if company is None:
            company = Company(id=company_id, name=f"Company {company_id}")
            s.add(company)
            s.flush()

        order = Order(
            company_id=company_id,
            order_number=order_number,
            created_at=created_at,
            status=OrderStatus.PAID,
            total_amount=total_amount,
            currency=currency,
            external_id=external_id,
            subtotal=total_amount,
            tax_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            discount_amount=Decimal("0"),
            delivery_date=delivery_date,
            internal_notes=internal_notes,
        )
        s.add(order)
        s.flush()

        for idx in range(items_count):
            unit_price = (total_amount / max(items_count, 1)).quantize(Decimal("0.01"))
            quantity = 1
            item = OrderItem(
                order_id=order.id,
                sku=f"SKU-{order.id}-{idx}",
                name="Item",
                unit_price=unit_price,
                quantity=quantity,
                total_price=unit_price * quantity,
                cost_price=Decimal("0"),
            )
            s.add(item)

        s.commit()
        s.refresh(order)
        return int(order.id)


def _parse_csv(text: str) -> list[list[str]]:
    buf = StringIO(text)
    return list(csv.reader(buf))


async def test_orders_csv_store_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    notes = json.dumps(
        {
            "kaspi": {
                "preOrder": True,
                "plannedDeliveryDate": "2026-02-12T00:00:00Z",
                "reservationDate": "2026-02-11T00:00:00Z",
            }
        }
    )
    _seed_order(
        company_id=1001,
        order_number="ORD-1001-A",
        created_at=now,
        total_amount=Decimal("25.00"),
        external_id="EXT-1",
        items_count=2,
        delivery_date="2026-02-10T09:00:00Z",
        internal_notes=notes,
    )

    resp = await async_client.get(
        "/api/v1/reports/orders.csv?limit=1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")

    rows = _parse_csv(resp.text)
    assert rows
    assert rows[0] == [
        "order_id",
        "created_at",
        "status",
        "total_amount",
        "currency",
        "external_id",
        "items_count",
        "delivery_date",
        "kaspi_preorder",
        "kaspi_planned_delivery_date",
        "kaspi_reservation_date",
    ]
    assert len(rows) == 2
    header = rows[0]
    row = rows[1]
    values = dict(zip(header, row, strict=True))
    assert values["delivery_date"] == "2026-02-10T09:00:00Z"
    assert values["kaspi_preorder"] == "True"
    assert values["kaspi_planned_delivery_date"] == "2026-02-12T00:00:00Z"
    assert values["kaspi_reservation_date"] == "2026-02-11T00:00:00Z"


async def test_orders_csv_tenant_isolation(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(
        company_id=2001,
        order_number="ORD-2001-A",
        created_at=now,
        total_amount=Decimal("10.00"),
    )

    resp = await async_client.get(
        "/api/v1/reports/orders.csv?company_id=2001",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 404, resp.text


async def test_orders_csv_platform_admin(async_client, auth_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(
        company_id=1001,
        order_number="ORD-1001-B",
        created_at=now,
        total_amount=Decimal("15.00"),
    )

    resp = await async_client.get(
        "/api/v1/reports/orders.csv?company_id=1001&limit=10",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    assert rows


async def test_orders_csv_date_filters(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(
        company_id=1001,
        order_number="ORD-1001-OLD",
        created_at=now - timedelta(days=3),
        total_amount=Decimal("11.00"),
    )
    _seed_order(
        company_id=1001,
        order_number="ORD-1001-NEW",
        created_at=now - timedelta(hours=1),
        total_amount=Decimal("12.00"),
    )

    date_from = (now - timedelta(days=1)).isoformat()
    resp = await async_client.get(
        "/api/v1/reports/orders.csv",
        headers=company_a_admin_headers,
        params={"date_from": date_from},
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    assert len(rows) == 2
