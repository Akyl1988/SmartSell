from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.company import Company
from app.models.order import Order, OrderItem, OrderStatus

pytestmark = pytest.mark.asyncio


def _seed_order_item(
    *,
    company_id: int,
    order_number: str,
    created_at: datetime,
    total_amount: Decimal,
    sku: str = "SKU-1",
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
            currency="KZT",
            subtotal=total_amount,
            tax_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            discount_amount=Decimal("0"),
        )
        s.add(order)
        s.flush()

        item = OrderItem(
            order_id=order.id,
            sku=sku,
            name="Item",
            unit_price=total_amount,
            quantity=1,
            total_price=total_amount,
            cost_price=Decimal("0"),
        )
        s.add(item)
        s.commit()
        s.refresh(item)
        return int(item.id)


def _parse_csv(text: str) -> list[list[str]]:
    buf = StringIO(text)
    return list(csv.reader(buf))


async def test_order_items_csv_store_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order_item(
        company_id=1001,
        order_number="ORD-ITEM-1",
        created_at=now,
        total_amount=Decimal("19.00"),
    )

    resp = await async_client.get(
        "/api/v1/reports/order_items.csv?limit=5",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")

    rows = _parse_csv(resp.text)
    assert rows
    assert rows[0] == [
        "order_id",
        "order_created_at",
        "order_status",
        "order_external_id",
        "item_id",
        "product_id",
        "sku",
        "name",
        "quantity",
        "unit_price",
        "total_price",
        "currency",
        "created_at",
    ]
    assert len(rows) >= 2


async def test_order_items_csv_tenant_isolation(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order_item(
        company_id=2001,
        order_number="ORD-ITEM-2",
        created_at=now,
        total_amount=Decimal("21.00"),
    )

    resp = await async_client.get(
        "/api/v1/reports/order_items.csv?company_id=2001",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 404, resp.text


async def test_order_items_csv_platform_admin(async_client, auth_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order_item(
        company_id=1001,
        order_number="ORD-ITEM-3",
        created_at=now,
        total_amount=Decimal("22.00"),
    )

    resp = await async_client.get(
        "/api/v1/reports/order_items.csv?company_id=1001&limit=10",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    assert rows


async def test_order_items_csv_audit_log(async_client, company_a_admin_headers, test_db, monkeypatch):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order_item(
        company_id=1001,
        order_number="ORD-ITEM-AUDIT",
        created_at=now,
        total_amount=Decimal("24.00"),
    )

    events: list[dict[str, object]] = []

    def _capture_event(**kwargs):
        events.append(kwargs)

    from app.core import logging as logging_mod

    monkeypatch.setattr(logging_mod.audit_logger, "log_system_event", _capture_event)

    resp = await async_client.get(
        "/api/v1/reports/order_items.csv?limit=1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert events
    event = events[-1]
    assert event.get("event") == "report_order_items_csv"
    meta = event.get("meta") or {}
    assert meta.get("company_id") == 1001
    assert meta.get("limit") == 1
