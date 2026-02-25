from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.billing import Subscription
from app.models.company import Company
from app.models.preorder import Preorder, PreorderItem, PreorderStatus

pytestmark = pytest.mark.asyncio


def _seed_preorder(
    *,
    company_id: int,
    created_at: datetime,
    total_amount: Decimal,
    items_count: int = 1,
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

        preorder = Preorder(
            company_id=company_id,
            status=PreorderStatus.NEW,
            currency="KZT",
            total=total_amount,
            customer_name="Alice",
            customer_phone="+70000000000",
            source="web",
            external_id="EXT-PO",
            created_at=created_at,
        )
        s.add(preorder)
        s.flush()

        for idx in range(items_count):
            item = PreorderItem(
                preorder_id=preorder.id,
                sku=f"SKU-{preorder.id}-{idx}",
                name="Item",
                qty=1,
                price=total_amount,
            )
            s.add(item)

        s.commit()
        s.refresh(preorder)
        return int(preorder.id)


def _parse_csv(text: str) -> list[list[str]]:
    buf = StringIO(text)
    return list(csv.reader(buf))


async def test_preorders_csv_ok_for_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    preorder_id = _seed_preorder(company_id=1001, created_at=now, total_amount=Decimal("25.00"), items_count=2)

    resp = await async_client.get(
        "/api/v1/reports/preorders.csv?limit=1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")

    rows = _parse_csv(resp.text)
    assert rows
    assert rows[0] == [
        "preorder_id",
        "company_id",
        "created_at",
        "status",
        "total_amount",
        "currency",
        "customer_name",
        "customer_phone",
        "items_count",
        "source",
        "external_id",
        "fulfilled_order_id",
        "fulfilled_at",
    ]
    assert len(rows) >= 2
    header = rows[0]
    row = rows[1]
    values = dict(zip(header, row, strict=True))
    assert values["preorder_id"] == str(preorder_id)
    assert values["company_id"] == "1001"


@pytest.mark.no_subscription
async def test_preorders_csv_subscription_required(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/reports/preorders.csv",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


async def test_preorders_csv_feature_required(async_client, async_db_session, company_a_admin_headers):
    sub = (
        (
            await async_db_session.execute(
                select(Subscription).where(
                    Subscription.company_id == 1001,
                    Subscription.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    assert sub is not None
    sub.plan = "basic"
    sub.status = "active"
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/reports/preorders.csv",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


async def test_preorders_csv_rbac(async_client, company_a_employee_headers):
    resp = await async_client.get(
        "/api/v1/reports/preorders.csv",
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
