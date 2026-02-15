from __future__ import annotations

import uuid

import pytest

from app.models.order import Order, OrderStatus
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_order(async_db_session, company_id: int) -> Order:
    order = Order(
        company_id=company_id,
        order_number=f"ORD-{uuid.uuid4().hex[:10]}",
        status=OrderStatus.PENDING,
    )
    async_db_session.add(order)
    await async_db_session.commit()
    await async_db_session.refresh(order)
    return order


async def test_orders_employee_can_list(async_client, company_a_employee_headers, company_a_admin_headers):
    resp = await async_client.get("/api/v1/orders", headers=company_a_employee_headers)
    assert resp.status_code == 200, resp.text


async def test_orders_employee_forbidden_on_patch(
    async_client,
    db_session,
    async_db_session,
    company_a_employee_headers,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    order = await _create_order(async_db_session, user_a.company_id)

    resp = await async_client.patch(
        f"/api/v1/orders/{order.id}",
        json={"internal_notes": "employee block"},
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")


async def test_orders_admin_can_patch(async_client, db_session, async_db_session, company_a_admin_headers):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    order = await _create_order(async_db_session, user_a.company_id)

    resp = await async_client.patch(
        f"/api/v1/orders/{order.id}",
        json={"internal_notes": "updated"},
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("internal_notes") == "updated"


async def test_orders_platform_admin_can_access_other_company(
    async_client,
    db_session,
    async_db_session,
    company_b_admin_headers,
    auth_headers,
):
    user_b = _get_user_by_phone(db_session, "+70000020001")
    order = await _create_order(async_db_session, user_b.company_id)

    resp = await async_client.get(f"/api/v1/orders/{order.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
