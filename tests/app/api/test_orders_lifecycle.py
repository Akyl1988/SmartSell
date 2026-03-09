from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.user import User
from app.models.warehouse import ProductStock, StockMovement, Warehouse

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _seed_inventory(async_db_session, company_id: int, *, quantity: int = 5) -> tuple[Product, Warehouse]:
    suffix = uuid4().hex[:8]
    product = Product(
        company_id=company_id,
        name=f"Order Product {suffix}",
        slug=f"order-product-{suffix}",
        sku=f"ORD-{suffix}",
        price=100,
        stock_quantity=quantity,
    )
    warehouse = Warehouse(company_id=company_id, name="Main", is_main=True)
    async_db_session.add_all([product, warehouse])
    await async_db_session.commit()
    await async_db_session.refresh(product)
    await async_db_session.refresh(warehouse)

    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=quantity, reserved_quantity=0)
    async_db_session.add(stock)
    await async_db_session.commit()
    return product, warehouse


async def test_orders_lifecycle_happy_path(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=5)

    created = await async_client.post(
        "/api/v1/orders",
        json={
            "source": "manual",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "unit_price": "100.00",
                    "quantity": 2,
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    order_id = created.json().get("id")
    assert order_id

    confirmed = await async_client.post(
        f"/api/v1/orders/{order_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("status") == "confirmed"

    stock = (
        (
            await async_db_session.execute(
                select(ProductStock)
                .where(
                    ProductStock.product_id == product.id,
                    ProductStock.warehouse_id == warehouse.id,
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    assert stock.reserved_quantity == 2

    shipped = await async_client.post(
        f"/api/v1/orders/{order_id}/ship",
        headers=company_a_admin_headers,
    )
    assert shipped.status_code == 200, shipped.text
    assert shipped.json().get("status") == "shipped"

    fulfilled = await async_client.post(
        f"/api/v1/orders/{order_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json().get("status") == "completed"

    stock = (
        (
            await async_db_session.execute(
                select(ProductStock)
                .where(
                    ProductStock.product_id == product.id,
                    ProductStock.warehouse_id == warehouse.id,
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    assert stock.quantity == 3
    assert stock.reserved_quantity == 0

    movements = (
        (
            await async_db_session.execute(
                select(StockMovement).where(
                    StockMovement.reference_type == "order",
                    StockMovement.reference_id == order_id,
                    StockMovement.product_id == product.id,
                )
            )
        )
        .scalars()
        .all()
    )
    types = {move.movement_type for move in movements}
    assert "reserve" in types
    assert "fulfill" in types


async def test_orders_invalid_transition_fulfill_before_confirm(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=5)

    created = await async_client.post(
        "/api/v1/orders",
        json={
            "source": "manual",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "unit_price": "100.00",
                    "quantity": 1,
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    order_id = created.json().get("id")

    fulfilled = await async_client.post(
        f"/api/v1/orders/{order_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 409, fulfilled.text
    assert fulfilled.json().get("code") == "INVALID_ORDER_STATUS"


async def test_orders_cancel_releases_reservation(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=3)

    created = await async_client.post(
        "/api/v1/orders",
        json={
            "source": "manual",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "unit_price": "50.00",
                    "quantity": 2,
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    order_id = created.json().get("id")

    await async_client.post(
        f"/api/v1/orders/{order_id}/confirm",
        headers=company_a_admin_headers,
    )

    cancelled = await async_client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json().get("status") == "cancelled"

    stock = (
        (
            await async_db_session.execute(
                select(ProductStock)
                .where(
                    ProductStock.product_id == product.id,
                    ProductStock.warehouse_id == warehouse.id,
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    assert stock.reserved_quantity == 0


async def test_orders_double_fulfill_rejected(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=4)

    created = await async_client.post(
        "/api/v1/orders",
        json={
            "source": "manual",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "unit_price": "100.00",
                    "quantity": 2,
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    order_id = created.json().get("id")

    await async_client.post(
        f"/api/v1/orders/{order_id}/confirm",
        headers=company_a_admin_headers,
    )
    await async_client.post(
        f"/api/v1/orders/{order_id}/fulfill",
        headers=company_a_admin_headers,
    )

    fulfilled_again = await async_client.post(
        f"/api/v1/orders/{order_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled_again.status_code == 409, fulfilled_again.text
    assert fulfilled_again.json().get("code") == "ORDER_ALREADY_FULFILLED"


async def test_orders_tenant_isolation_confirm_denied(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=4)

    created = await async_client.post(
        "/api/v1/orders",
        json={
            "source": "manual",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "unit_price": "100.00",
                    "quantity": 1,
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    order_id = created.json().get("id")

    resp = await async_client.post(
        f"/api/v1/orders/{order_id}/confirm",
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 403, resp.text
