from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

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


async def test_preorders_lifecycle(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product = await _create_product(async_db_session, user_a.company_id)

    created = await async_client.post(
        "/api/v1/preorders",
        json={"product_id": product.id, "qty": 1, "customer_name": "Alice"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("status") == "confirmed"

    cancelled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json().get("status") == "cancelled"

    invalid = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert invalid.status_code == 422, invalid.text
    payload = invalid.json()
    assert payload.get("code") in {"INVALID_PREORDER_STATUS", "VALIDATION_ERROR"}
