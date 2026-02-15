from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.order import Order
from app.models.product import Product
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_product(async_db_session, company_id: int) -> Product:
    suffix = uuid.uuid4().hex[:6]
    product = Product(
        company_id=company_id,
        name=f"Preorder Product {suffix}",
        slug=f"preorder-product-{suffix}",
        sku=f"PRESKU{suffix.upper()}",
        price=Decimal("100.00"),
        is_active=True,
        is_preorder_enabled=True,
        preorder_until=int((datetime.utcnow() + timedelta(days=1)).timestamp()),
    )
    async_db_session.add(product)
    await async_db_session.commit()
    await async_db_session.refresh(product)
    return product


async def test_preorders_convert_to_order(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product = await _create_product(async_db_session, user_a.company_id)

    created = await async_client.post(
        "/api/v1/preorders",
        json={"product_id": product.id, "qty": 2, "customer_name": "Alice"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")

    invalid = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/convert-to-order",
        headers=company_a_admin_headers,
    )
    assert invalid.status_code == 422, invalid.text

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    converted = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/convert-to-order",
        headers=company_a_admin_headers,
    )
    assert converted.status_code == 200, converted.text
    converted_payload = converted.json()
    order_id = converted_payload.get("converted_order_id")
    assert order_id is not None
    assert converted_payload.get("status") == "converted"

    order = (
        await async_db_session.execute(select(Order).where(Order.id == order_id, Order.company_id == user_a.company_id))
    ).scalar_one_or_none()
    assert order is not None
    assert order.total_amount == Decimal("200.00")

    second = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/convert-to-order",
        headers=company_a_admin_headers,
    )
    assert second.status_code == 409, second.text
    payload = second.json()
    assert payload.get("code") in {"PREORDER_ALREADY_CONVERTED", "VALIDATION_ERROR"}
