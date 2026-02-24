from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.product import Product
from app.models.user import User
from app.models.warehouse import ProductStock, StockMovement, Warehouse

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _seed_inventory(async_db_session, company_id: int, *, quantity: int = 5) -> tuple[Product, Warehouse]:
    product = Product(
        company_id=company_id,
        name="Inventory Product",
        slug="inventory-product",
        sku="INV-001",
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


async def test_preorder_confirm_reserves_stock_idempotent(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=4)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 2,
                    "price": "100.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")
    assert preorder_id

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

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
    assert stock.reserved_quantity == 2

    confirmed_again = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed_again.status_code == 200, confirmed_again.text

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
    assert stock.reserved_quantity == 2

    moves = (
        (
            await async_db_session.execute(
                select(StockMovement).where(
                    StockMovement.reference_type == "preorder",
                    StockMovement.reference_id == preorder_id,
                    StockMovement.movement_type == "reserve",
                    StockMovement.product_id == product.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(moves) == 1


async def test_preorder_confirm_insufficient_stock_returns_422(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=1)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 2,
                    "price": "100.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 422, confirmed.text
    assert confirmed.json().get("code") == "INSUFFICIENT_STOCK"

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
    assert stock.reserved_quantity == 0


async def test_preorder_confirm_missing_warehouse_returns_422(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    suffix = uuid4().hex[:8]
    product = Product(
        company_id=user_a.company_id,
        name=f"Inventory Product {suffix}",
        slug=f"inventory-product-{suffix}",
        sku=f"INV-{suffix}",
        price=100,
        stock_quantity=5,
    )
    async_db_session.add(product)
    await async_db_session.commit()
    await async_db_session.refresh(product)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 1,
                    "price": "100.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 422, confirmed.text
    assert confirmed.json().get("code") == "WAREHOUSE_NOT_CONFIGURED"


async def test_preorder_confirm_uses_main_warehouse_when_multiple(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    suffix = uuid4().hex[:8]
    product = Product(
        company_id=user_a.company_id,
        name=f"Inventory Product {suffix}",
        slug=f"inventory-product-{suffix}",
        sku=f"INV-{suffix}",
        price=100,
        stock_quantity=5,
    )
    main_wh = Warehouse(company_id=user_a.company_id, name="Main", is_main=True)
    extra_wh = Warehouse(company_id=user_a.company_id, name="Extra", is_main=False)
    async_db_session.add_all([product, main_wh, extra_wh])
    await async_db_session.commit()
    await async_db_session.refresh(product)
    await async_db_session.refresh(main_wh)
    await async_db_session.refresh(extra_wh)

    stock_main = ProductStock(
        product_id=product.id,
        warehouse_id=main_wh.id,
        quantity=5,
        reserved_quantity=0,
    )
    stock_extra = ProductStock(
        product_id=product.id,
        warehouse_id=extra_wh.id,
        quantity=5,
        reserved_quantity=0,
    )
    async_db_session.add_all([stock_main, stock_extra])
    await async_db_session.commit()

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 2,
                    "price": "100.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    main_stock = (
        (
            await async_db_session.execute(
                select(ProductStock)
                .where(
                    ProductStock.product_id == product.id,
                    ProductStock.warehouse_id == main_wh.id,
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    extra_stock = (
        (
            await async_db_session.execute(
                select(ProductStock)
                .where(
                    ProductStock.product_id == product.id,
                    ProductStock.warehouse_id == extra_wh.id,
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    assert main_stock.reserved_quantity == 2
    assert extra_stock.reserved_quantity == 0

    move = (
        (
            await async_db_session.execute(
                select(StockMovement).where(
                    StockMovement.reference_type == "preorder",
                    StockMovement.reference_id == preorder_id,
                    StockMovement.movement_type == "reserve",
                    StockMovement.product_id == product.id,
                )
            )
        )
        .scalars()
        .one()
    )
    reserved_stock = await async_db_session.get(ProductStock, move.stock_id)
    assert reserved_stock.warehouse_id == main_wh.id


async def test_preorder_confirm_tenant_isolation(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    company_b = await async_db_session.get(Company, 2001)
    if company_b is None:
        company_b = Company(id=2001, name="Company 2001")
        async_db_session.add(company_b)
        await async_db_session.commit()
        await async_db_session.refresh(company_b)
    suffix = uuid4().hex[:8]
    product_b = Product(
        company_id=company_b.id,
        name=f"Other Product {suffix}",
        slug=f"other-product-{suffix}",
        sku=f"OTH-{suffix}",
        price=120,
        stock_quantity=2,
    )
    warehouse_b = Warehouse(company_id=company_b.id, name="Other Main", is_main=True)
    async_db_session.add_all([product_b, warehouse_b])
    await async_db_session.commit()
    await async_db_session.refresh(product_b)
    await async_db_session.refresh(warehouse_b)

    stock_b = ProductStock(
        product_id=product_b.id,
        warehouse_id=warehouse_b.id,
        quantity=2,
        reserved_quantity=0,
    )
    async_db_session.add(stock_b)
    await async_db_session.commit()

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product_b.id,
                    "sku": product_b.sku,
                    "name": product_b.name,
                    "qty": 1,
                    "price": "120.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 404, confirmed.text
    assert confirmed.json().get("code") == "PRODUCT_NOT_FOUND"

    stock = (
        (
            await async_db_session.execute(
                select(ProductStock).where(
                    ProductStock.product_id == product_b.id,
                    ProductStock.warehouse_id == warehouse_b.id,
                )
            )
        )
        .scalars()
        .one()
    )
    assert stock.reserved_quantity == 0


async def test_preorder_cancel_releases_stock_idempotent(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=3)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 1,
                    "price": "50.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )

    cancelled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text

    await async_db_session.rollback()

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

    cancelled_again = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled_again.status_code == 200, cancelled_again.text

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

    moves = (
        (
            await async_db_session.execute(
                select(StockMovement).where(
                    StockMovement.reference_type == "preorder",
                    StockMovement.reference_id == preorder_id,
                    StockMovement.movement_type == "release",
                    StockMovement.product_id == product.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(moves) == 1


async def test_preorder_cancel_partial_reserve_releases_remaining(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=5)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 3,
                    "price": "100.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )

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
    stock.reserved_quantity = 1
    await async_db_session.commit()

    cancelled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text

    await async_db_session.rollback()

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

    moves = (
        (
            await async_db_session.execute(
                select(StockMovement).where(
                    StockMovement.reference_type == "preorder",
                    StockMovement.reference_id == preorder_id,
                    StockMovement.movement_type == "release",
                    StockMovement.product_id == product.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(moves) == 1


async def test_preorder_fulfill_decrements_stock_idempotent(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product, warehouse = await _seed_inventory(async_db_session, user_a.company_id, quantity=5)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "currency": "KZT",
            "customer_name": "Alice",
            "items": [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "qty": 2,
                    "price": "100.00",
                }
            ],
        },
        headers=company_a_admin_headers,
    )
    preorder_id = created.json().get("id")

    await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )

    fulfilled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text

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
    assert stock.quantity == 3
    assert stock.reserved_quantity == 0

    fulfilled_again = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled_again.status_code == 200, fulfilled_again.text

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

    moves = (
        (
            await async_db_session.execute(
                select(StockMovement).where(
                    StockMovement.reference_type == "preorder",
                    StockMovement.reference_id == preorder_id,
                    StockMovement.movement_type == "fulfill",
                    StockMovement.product_id == product.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(moves) == 1
