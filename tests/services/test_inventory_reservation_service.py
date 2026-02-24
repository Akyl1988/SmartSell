from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.models.warehouse import MovementType, ProductStock, StockMovement, Warehouse
from app.services.inventory_reservations import fulfill_reservation, release_and_log, reserve_and_log

pytestmark = pytest.mark.asyncio


async def _create_main_warehouse(async_db_session, company):
    warehouse = Warehouse(company_id=company.id, name="Main", is_main=True)
    async_db_session.add(warehouse)
    await async_db_session.commit()
    await async_db_session.refresh(warehouse)
    return warehouse


async def test_reserve_and_release_happy_path(async_db_session, factory):
    company = await factory["create_company"]()
    product = await factory["create_product"](company=company, stock_quantity=5)
    warehouse = await _create_main_warehouse(async_db_session, company)
    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=5, reserved_quantity=0)
    async_db_session.add(stock)
    await async_db_session.commit()

    result = await reserve_and_log(
        async_db_session,
        tenant_id=company.id,
        product_id=product.id,
        qty=3,
        reference_type="preorder",
        reference_id=100,
    )
    assert result["reserved"] == 3
    assert result["available"] == 2

    result = await release_and_log(
        async_db_session,
        tenant_id=company.id,
        product_id=product.id,
        qty=2,
        reference_type="preorder",
        reference_id=100,
    )
    assert result["reserved"] == 1

    movements = (
        (await async_db_session.execute(select(StockMovement).where(StockMovement.product_id == product.id)))
        .scalars()
        .all()
    )
    types = {m.movement_type for m in movements}
    assert MovementType.RESERVE.value in types
    assert MovementType.RELEASE.value in types


async def test_reserve_insufficient_stock(async_db_session, factory):
    company = await factory["create_company"]()
    product = await factory["create_product"](company=company, stock_quantity=1)
    warehouse = await _create_main_warehouse(async_db_session, company)
    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=1, reserved_quantity=0)
    async_db_session.add(stock)
    await async_db_session.commit()

    with pytest.raises(SmartSellValidationError) as exc:
        await reserve_and_log(
            async_db_session,
            tenant_id=company.id,
            product_id=product.id,
            qty=2,
            reference_type="preorder",
            reference_id=200,
        )
    assert exc.value.code == "INSUFFICIENT_STOCK"


async def test_release_invalid(async_db_session, factory):
    company = await factory["create_company"]()
    product = await factory["create_product"](company=company, stock_quantity=5)
    warehouse = await _create_main_warehouse(async_db_session, company)
    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=5, reserved_quantity=1)
    async_db_session.add(stock)
    await async_db_session.commit()

    with pytest.raises(SmartSellValidationError) as exc:
        await release_and_log(
            async_db_session,
            tenant_id=company.id,
            product_id=product.id,
            qty=2,
            reference_type="preorder",
            reference_id=300,
        )
    assert exc.value.code == "INVALID_RELEASE"


async def test_fulfill_reservation(async_db_session, factory):
    company = await factory["create_company"]()
    product = await factory["create_product"](company=company, stock_quantity=6)
    warehouse = await _create_main_warehouse(async_db_session, company)
    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=6, reserved_quantity=4)
    async_db_session.add(stock)
    await async_db_session.commit()

    result = await fulfill_reservation(
        async_db_session,
        tenant_id=company.id,
        product_id=product.id,
        qty=3,
        reference_type="preorder",
        reference_id=400,
    )
    assert result["on_hand"] == 3
    assert result["reserved"] == 1


async def test_warehouse_not_configured(async_db_session, factory):
    company = await factory["create_company"]()
    product = await factory["create_product"](company=company, stock_quantity=3)

    with pytest.raises(SmartSellValidationError) as exc:
        await reserve_and_log(
            async_db_session,
            tenant_id=company.id,
            product_id=product.id,
            qty=1,
            reference_type="preorder",
            reference_id=500,
        )
    assert exc.value.code == "WAREHOUSE_NOT_CONFIGURED"


async def test_tenant_isolation(async_db_session, factory):
    company_a = await factory["create_company"]()
    company_b = await factory["create_company"](name="Other")
    product_b = await factory["create_product"](company=company_b, stock_quantity=4)

    with pytest.raises(NotFoundError) as exc:
        await reserve_and_log(
            async_db_session,
            tenant_id=company_a.id,
            product_id=product_b.id,
            qty=1,
            reference_type="preorder",
            reference_id=600,
        )
    assert exc.value.code == "PRODUCT_NOT_FOUND"


async def test_double_reserve_insufficient(async_db_session, factory):
    company = await factory["create_company"]()
    product = await factory["create_product"](company=company, stock_quantity=1)
    warehouse = await _create_main_warehouse(async_db_session, company)
    stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=1, reserved_quantity=0)
    async_db_session.add(stock)
    await async_db_session.commit()

    await reserve_and_log(
        async_db_session,
        tenant_id=company.id,
        product_id=product.id,
        qty=1,
        reference_type="preorder",
        reference_id=700,
    )

    with pytest.raises(SmartSellValidationError) as exc:
        await reserve_and_log(
            async_db_session,
            tenant_id=company.id,
            product_id=product.id,
            qty=1,
            reference_type="preorder",
            reference_id=701,
        )
    assert exc.value.code == "INSUFFICIENT_STOCK"
