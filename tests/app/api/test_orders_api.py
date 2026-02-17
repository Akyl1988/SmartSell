from __future__ import annotations

import uuid

import pytest

from app.models.order import Order, OrderStatus
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_order(async_db_session, company_id: int, *, status: OrderStatus = OrderStatus.PENDING) -> Order:
    order = Order(
        company_id=company_id,
        order_number=f"ORD-{uuid.uuid4().hex[:10]}",
        status=status,
    )
    async_db_session.add(order)
    await async_db_session.commit()
    await async_db_session.refresh(order)
    return order


async def test_orders_list_company_scoped(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    user_b = _get_user_by_phone(db_session, "+70000020001")

    order_a = await _create_order(async_db_session, user_a.company_id)
    await _create_order(async_db_session, user_b.company_id)

    resp = await async_client.get("/api/v1/orders", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    items = payload.get("items") or []

    assert any(item.get("order_number") == order_a.order_number for item in items)
    assert all(item.get("company_id") == user_a.company_id for item in items)


async def test_orders_get_forbidden_across_tenants(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    order_a = await _create_order(async_db_session, user_a.company_id)

    resp = await async_client.get(f"/api/v1/orders/{order_a.id}", headers=company_b_admin_headers)
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") in {"FORBIDDEN", "AUTHORIZATION_ERROR"}
    assert payload.get("request_id")


async def test_orders_patch_forbidden_across_tenants(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    order_a = await _create_order(async_db_session, user_a.company_id)

    resp = await async_client.patch(
        f"/api/v1/orders/{order_a.id}",
        json={"internal_notes": "blocked"},
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") in {"FORBIDDEN", "AUTHORIZATION_ERROR"}
    assert payload.get("request_id")
