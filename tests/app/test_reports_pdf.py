from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.company import Company
from app.models.order import Order, OrderItem, OrderStatus

pytestmark = pytest.mark.asyncio


def _seed_order(
    *,
    company_id: int,
    created_at: datetime,
    total_amount: Decimal,
    items_count: int = 1,
    sku_prefix: str = "SKU",
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
            order_number=f"ORD-{company_id}-{int(created_at.timestamp())}",
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

        per_item = (total_amount / max(items_count, 1)).quantize(Decimal("0.01"))
        for idx in range(items_count):
            item = OrderItem(
                order_id=order.id,
                sku=f"{sku_prefix}-{order.id}-{idx}",
                name="Item",
                unit_price=per_item,
                quantity=1,
                total_price=per_item,
                cost_price=Decimal("0"),
            )
            s.add(item)

        s.commit()
        s.refresh(order)
        return int(order.id)


def _assert_pdf_response(resp) -> None:
    assert resp.status_code == 200, resp.text
    assert "application/pdf" in resp.headers.get("content-type", "")
    body = resp.content
    assert body
    assert body.startswith(b"%PDF")


async def test_orders_pdf_store_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(company_id=1001, created_at=now, total_amount=Decimal("19.00"), items_count=2)

    resp = await async_client.get(
        "/api/v1/reports/orders.pdf",
        headers=company_a_admin_headers,
        params={"limit": 5},
    )
    _assert_pdf_response(resp)


async def test_orders_pdf_tenant_isolation(async_client, company_a_admin_headers, test_db):
    _ = test_db
    resp = await async_client.get(
        "/api/v1/reports/orders.pdf",
        headers=company_a_admin_headers,
        params={"company_id": 2001},
    )
    assert resp.status_code == 404, resp.text


async def test_orders_pdf_platform_admin(async_client, auth_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(company_id=1001, created_at=now, total_amount=Decimal("21.00"))

    resp = await async_client.get(
        "/api/v1/reports/orders.pdf",
        headers=auth_headers,
        params={"companyId": 1001, "limit": 5},
    )
    _assert_pdf_response(resp)


async def test_orders_pdf_audit_log(async_client, company_a_admin_headers, test_db, monkeypatch):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(company_id=1001, created_at=now, total_amount=Decimal("23.00"))

    events: list[dict[str, object]] = []

    def _capture_event(**kwargs):
        events.append(kwargs)

    from app.core import logging as logging_mod

    monkeypatch.setattr(logging_mod.audit_logger, "log_system_event", _capture_event)

    date_from = (now - timedelta(days=1)).isoformat()
    date_to = now.isoformat()
    resp = await async_client.get(
        "/api/v1/reports/orders.pdf",
        headers=company_a_admin_headers,
        params={"limit": 1, "date_from": date_from, "date_to": date_to},
    )
    _assert_pdf_response(resp)
    assert events
    event = events[-1]
    assert event.get("event") == "report_orders_pdf"
    meta = event.get("meta") or {}
    assert meta.get("company_id") == 1001
    assert meta.get("limit") == 1
    assert meta.get("date_from") == date_from
    assert meta.get("date_to") == date_to
    assert meta.get("request_id")
    assert meta.get("rows_count") is not None


async def test_sales_pdf_store_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(company_id=1001, created_at=now, total_amount=Decimal("31.00"), items_count=1)

    resp = await async_client.get(
        "/api/v1/reports/sales.pdf",
        headers=company_a_admin_headers,
        params={"date_from": now.date().isoformat(), "date_to": now.date().isoformat()},
    )
    _assert_pdf_response(resp)


async def test_sales_pdf_tenant_isolation(async_client, company_a_admin_headers, test_db):
    _ = test_db
    resp = await async_client.get(
        "/api/v1/reports/sales.pdf",
        headers=company_a_admin_headers,
        params={"company_id": 2001},
    )
    assert resp.status_code == 404, resp.text


async def test_sales_pdf_platform_admin(async_client, auth_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_order(company_id=1001, created_at=now, total_amount=Decimal("35.00"), items_count=1)

    resp = await async_client.get(
        "/api/v1/reports/sales.pdf",
        headers=auth_headers,
        params={"companyId": 1001},
    )
    _assert_pdf_response(resp)
