from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.company import Company
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse

pytestmark = pytest.mark.asyncio


def _seed_inventory(
    *,
    company_id: int,
    warehouse_name: str,
    sku: str,
    on_hand: int,
    reserved: int,
) -> tuple[int, int]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        company = s.get(Company, company_id)
        if company is None:
            company = Company(id=company_id, name=f"Company {company_id}")
            s.add(company)
            s.flush()

        warehouse = Warehouse(company_id=company_id, name=warehouse_name, is_main=False)
        s.add(warehouse)
        s.flush()

        product = Product(
            company_id=company_id,
            name=f"Product {sku}",
            slug=f"product-{sku.lower()}",
            sku=sku,
            price=Decimal("10.00"),
            stock_quantity=on_hand,
            reserved_quantity=reserved,
            created_at=datetime.now(UTC),
        )
        s.add(product)
        s.flush()

        stock = ProductStock(
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity=on_hand,
            reserved_quantity=reserved,
        )
        s.add(stock)
        s.commit()
        return int(warehouse.id), int(product.id)


def _parse_csv(text: str) -> list[list[str]]:
    buf = StringIO(text)
    return list(csv.reader(buf))


async def test_inventory_csv_ok_for_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    warehouse_id, product_id = _seed_inventory(
        company_id=1001,
        warehouse_name="Main",
        sku="INV-1",
        on_hand=7,
        reserved=2,
    )

    resp = await async_client.get(
        "/api/v1/reports/inventory.csv?limit=5",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")

    rows = _parse_csv(resp.text)
    assert rows
    assert rows[0] == [
        "company_id",
        "warehouse_id",
        "warehouse_name",
        "product_id",
        "sku",
        "product_name",
        "on_hand",
        "reserved",
        "available",
        "updated_at",
    ]
    assert len(rows) >= 2
    header = rows[0]
    row = rows[1]
    values = dict(zip(header, row, strict=True))
    assert values["warehouse_id"] == str(warehouse_id)
    assert values["product_id"] == str(product_id)
    assert values["on_hand"] == "7"
    assert values["reserved"] == "2"
    assert values["available"] == "5"


async def test_inventory_csv_filter_by_warehouse(async_client, company_a_admin_headers, test_db):
    _ = test_db
    warehouse_id_a, _ = _seed_inventory(
        company_id=1001,
        warehouse_name="Main",
        sku="INV-A",
        on_hand=5,
        reserved=1,
    )
    _seed_inventory(
        company_id=1001,
        warehouse_name="Secondary",
        sku="INV-B",
        on_hand=8,
        reserved=0,
    )

    resp = await async_client.get(
        f"/api/v1/reports/inventory.csv?warehouseId={warehouse_id_a}",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    assert len(rows) >= 2
    for row in rows[1:]:
        assert row[1] == str(warehouse_id_a)


@pytest.mark.no_subscription
async def test_inventory_csv_subscription_required(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/reports/inventory.csv",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


async def test_inventory_csv_rbac(async_client, company_a_employee_headers):
    resp = await async_client.get(
        "/api/v1/reports/inventory.csv",
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
