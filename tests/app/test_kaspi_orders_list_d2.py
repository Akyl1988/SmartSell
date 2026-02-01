from datetime import datetime

import pytest

from app.models.order import Order, OrderSource, OrderStatus

pytestmark = pytest.mark.asyncio


async def _create_order(session, *, company_id: int, external_id: str, status: OrderStatus):
    o = Order(
        company_id=company_id,
        source=OrderSource.KASPI,
        status=status,
        external_id=external_id,
        order_number=f"ORD-{company_id}-{external_id}",
        created_at=datetime.utcnow(),
    )
    session.add(o)
    await session.commit()
    await session.refresh(o)
    return o


async def test_kaspi_orders_list_tenant_isolation(
    async_client,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    await _create_order(
        async_db_session,
        company_id=1001,
        external_id="A1",
        status=OrderStatus.PENDING,
    )
    await _create_order(
        async_db_session,
        company_id=2001,
        external_id="B1",
        status=OrderStatus.PENDING,
    )

    r = await async_client.get(
        "/api/v1/kaspi/orders?limit=50",
        headers=company_a_admin_headers,
    )
    assert r.status_code == 200
    data = r.json()
    ids = [x.get("external_id") for x in data["items"]]
    assert "A1" in ids
    assert "B1" not in ids


async def test_kaspi_orders_list_pagination(async_client, async_db_session, company_a_admin_headers):
    for i in range(3):
        await _create_order(
            async_db_session,
            company_id=1001,
            external_id=f"A{i}",
            status=OrderStatus.PENDING,
        )

    r1 = await async_client.get(
        "/api/v1/kaspi/orders?skip=0&limit=2",
        headers=company_a_admin_headers,
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert len(d1["items"]) == 2
    assert d1["has_more"] is True

    r2 = await async_client.get(
        "/api/v1/kaspi/orders?skip=2&limit=2",
        headers=company_a_admin_headers,
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert len(d2["items"]) >= 1
