from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.user import User
from app.models.warehouse import ProductStock, Warehouse

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_stock(async_db_session, company_id: int, *, quantity: int = 5) -> tuple[Product, Warehouse]:
    product = Product(
        company_id=company_id,
        name="Stocked",
        slug="stocked",
        sku="SKU-STK",
        price=10,
        stock_quantity=quantity,
    )
    warehouse = Warehouse(company_id=company_id, name="Main", is_main=True)
    async_db_session.add(product)
    async_db_session.add(warehouse)
    await async_db_session.commit()
    await async_db_session.refresh(product)
    await async_db_session.refresh(warehouse)

    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=quantity, reserved_quantity=0)
    async_db_session.add(stock)
    await async_db_session.commit()
    return product, warehouse


async def test_inventory_reserve_release_fulfill(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _create_stock(async_db_session, user_a.company_id, quantity=5)

    reserve = await async_client.post(
        "/api/v1/inventory/reservations/reserve",
        json={
            "product_id": product.id,
            "qty": 2,
            "reference_type": "preorder",
            "reference_id": 10,
        },
        headers=company_a_admin_headers,
    )
    assert reserve.status_code == 200, reserve.text
    payload = reserve.json()
    assert payload["reserved"] == 2
    assert payload["available"] == 3

    release = await async_client.post(
        "/api/v1/inventory/reservations/release",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 10,
        },
        headers=company_a_admin_headers,
    )
    assert release.status_code == 200, release.text
    assert release.json()["reserved"] == 1

    fulfill = await async_client.post(
        "/api/v1/inventory/reservations/fulfill",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 10,
        },
        headers=company_a_admin_headers,
    )
    assert fulfill.status_code == 200, fulfill.text
    assert fulfill.json()["on_hand"] == 4
    assert fulfill.json()["reserved"] == 0


async def test_inventory_fulfill_twice_rejected(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _create_stock(async_db_session, user_a.company_id, quantity=3)

    reserve = await async_client.post(
        "/api/v1/inventory/reservations/reserve",
        json={
            "product_id": product.id,
            "qty": 2,
            "reference_type": "preorder",
            "reference_id": 11,
        },
        headers=company_a_admin_headers,
    )
    assert reserve.status_code == 200, reserve.text

    fulfill = await async_client.post(
        "/api/v1/inventory/reservations/fulfill",
        json={
            "product_id": product.id,
            "qty": 2,
            "reference_type": "preorder",
            "reference_id": 11,
        },
        headers=company_a_admin_headers,
    )
    assert fulfill.status_code == 200, fulfill.text

    fulfill_again = await async_client.post(
        "/api/v1/inventory/reservations/fulfill",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 11,
        },
        headers=company_a_admin_headers,
    )
    assert fulfill_again.status_code == 409, fulfill_again.text
    assert fulfill_again.json().get("code") == "RESERVATION_ALREADY_FULFILLED"


async def test_inventory_release_after_fulfill_rejected(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _create_stock(async_db_session, user_a.company_id, quantity=3)

    reserve = await async_client.post(
        "/api/v1/inventory/reservations/reserve",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 12,
        },
        headers=company_a_admin_headers,
    )
    assert reserve.status_code == 200, reserve.text

    fulfill = await async_client.post(
        "/api/v1/inventory/reservations/fulfill",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 12,
        },
        headers=company_a_admin_headers,
    )
    assert fulfill.status_code == 200, fulfill.text

    release = await async_client.post(
        "/api/v1/inventory/reservations/release",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 12,
        },
        headers=company_a_admin_headers,
    )
    assert release.status_code == 409, release.text
    assert release.json().get("code") == "RESERVATION_ALREADY_FULFILLED"


async def test_inventory_release_invalid(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _create_stock(async_db_session, user_a.company_id, quantity=2)

    release = await async_client.post(
        "/api/v1/inventory/reservations/release",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 20,
        },
        headers=company_a_admin_headers,
    )
    assert release.status_code == 422, release.text
    assert release.json().get("code") == "INVALID_RELEASE"


async def test_inventory_tenant_isolation(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, _warehouse = await _create_stock(async_db_session, user_a.company_id, quantity=2)

    forbidden = await async_client.post(
        "/api/v1/inventory/reservations/reserve",
        json={
            "product_id": product.id,
            "qty": 1,
            "reference_type": "preorder",
            "reference_id": 30,
        },
        headers=company_b_admin_headers,
    )
    assert forbidden.status_code == 404, forbidden.text


async def test_inventory_movement_rejects_below_reserved(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _create_stock(async_db_session, user_a.company_id, quantity=2)

    stock = (
        (
            await async_db_session.execute(
                select(ProductStock).where(
                    ProductStock.product_id == product.id,
                    ProductStock.warehouse_id == warehouse.id,
                )
            )
        )
        .scalars()
        .one()
    )
    stock.reserved_quantity = 2
    stock.quantity = 2
    await async_db_session.commit()

    movement = await async_client.post(
        "/api/v1/inventory/movements",
        json={
            "warehouse_id": warehouse.id,
            "product_id": product.id,
            "qty_delta": -1,
            "reason": "test",
        },
        headers=company_a_admin_headers,
    )
    assert movement.status_code == 422, movement.text
    assert movement.json().get("code") == "NEGATIVE_STOCK"
