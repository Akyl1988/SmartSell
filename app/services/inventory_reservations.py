"""Inventory reservation service (async, tenant-safe)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.models.product import Product
from app.models.warehouse import MovementType, ProductStock, StockMovement, Warehouse


def _validate_qty(qty: int) -> int:
    qty_int = int(qty or 0)
    if qty_int <= 0:
        raise SmartSellValidationError("Quantity must be positive", "INVALID_QTY", http_status=422)
    return qty_int


async def _get_product(db: AsyncSession, *, tenant_id: int, product_id: int) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id, Product.company_id == tenant_id))
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    return product


async def _get_warehouse(db: AsyncSession, *, tenant_id: int, warehouse_id: int) -> Warehouse:
    result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.company_id == tenant_id,
            Warehouse.is_active.is_(True),
            Warehouse.is_archived.is_(False),
        )
    )
    warehouse = result.scalar_one_or_none()
    if not warehouse:
        raise NotFoundError("Warehouse not found", "WAREHOUSE_NOT_FOUND")
    return warehouse


async def _get_default_warehouse(db: AsyncSession, *, tenant_id: int) -> Warehouse:
    result = await db.execute(
        select(Warehouse)
        .where(
            Warehouse.company_id == tenant_id,
            Warehouse.is_active.is_(True),
            Warehouse.is_archived.is_(False),
        )
        .order_by(Warehouse.is_main.desc(), Warehouse.id.asc())
    )
    warehouse = result.scalars().first()
    if not warehouse:
        raise SmartSellValidationError(
            "Warehouse is not configured",
            "WAREHOUSE_NOT_CONFIGURED",
            http_status=422,
        )
    return warehouse


async def _get_or_create_stock(
    db: AsyncSession,
    *,
    product: Product,
    warehouse: Warehouse,
) -> ProductStock:
    result = await db.execute(
        select(ProductStock)
        .where(
            ProductStock.product_id == product.id,
            ProductStock.warehouse_id == warehouse.id,
        )
        .with_for_update()
    )
    stock = result.scalar_one_or_none()
    if stock is not None:
        return stock

    base_qty = int(getattr(product, "stock_quantity", 0) or 0)
    stock = ProductStock(
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity=base_qty,
        reserved_quantity=0,
    )
    db.add(stock)
    await db.flush()
    return stock


def _build_result(stock: ProductStock) -> dict[str, int]:
    return {
        "product_id": int(stock.product_id),
        "warehouse_id": int(stock.warehouse_id),
        "on_hand": int(stock.quantity),
        "reserved": int(stock.reserved_quantity),
        "available": int(stock.available_quantity),
    }


async def reserve_and_log(
    db: AsyncSession,
    *,
    tenant_id: int,
    product_id: int,
    qty: int,
    reference_type: str,
    reference_id: int,
    warehouse_id: int | None = None,
) -> dict[str, int]:
    qty_int = _validate_qty(qty)
    product = await _get_product(db, tenant_id=tenant_id, product_id=product_id)
    if warehouse_id is None:
        warehouse = await _get_default_warehouse(db, tenant_id=tenant_id)
    else:
        warehouse = await _get_warehouse(db, tenant_id=tenant_id, warehouse_id=warehouse_id)

    stock = await _get_or_create_stock(db, product=product, warehouse=warehouse)
    if stock.available_quantity < qty_int:
        raise SmartSellValidationError("Insufficient stock", "INSUFFICIENT_STOCK", http_status=422)

    stock.reserved_quantity = int(stock.reserved_quantity) + qty_int

    movement = StockMovement(
        stock_id=stock.id,
        product_id=product.id,
        movement_type=MovementType.RESERVE.value,
        quantity=qty_int,
        previous_quantity=int(stock.quantity),
        new_quantity=int(stock.quantity),
        reference_type=reference_type,
        reference_id=reference_id,
        reason="reserve",
    )
    db.add(movement)
    await db.flush()

    return _build_result(stock)


async def release_and_log(
    db: AsyncSession,
    *,
    tenant_id: int,
    product_id: int,
    qty: int,
    reference_type: str,
    reference_id: int,
    warehouse_id: int | None = None,
) -> dict[str, int]:
    qty_int = _validate_qty(qty)
    product = await _get_product(db, tenant_id=tenant_id, product_id=product_id)
    if warehouse_id is None:
        warehouse = await _get_default_warehouse(db, tenant_id=tenant_id)
    else:
        warehouse = await _get_warehouse(db, tenant_id=tenant_id, warehouse_id=warehouse_id)

    stock = await _get_or_create_stock(db, product=product, warehouse=warehouse)
    if int(stock.reserved_quantity) < qty_int:
        raise SmartSellValidationError("Insufficient reserved stock", "INVALID_RELEASE", http_status=422)

    stock.reserved_quantity = int(stock.reserved_quantity) - qty_int

    movement = StockMovement(
        stock_id=stock.id,
        product_id=product.id,
        movement_type=MovementType.RELEASE.value,
        quantity=qty_int,
        previous_quantity=int(stock.quantity),
        new_quantity=int(stock.quantity),
        reference_type=reference_type,
        reference_id=reference_id,
        reason="release",
    )
    db.add(movement)
    await db.flush()

    return _build_result(stock)


async def fulfill_reservation(
    db: AsyncSession,
    *,
    tenant_id: int,
    product_id: int,
    qty: int,
    reference_type: str,
    reference_id: int,
    warehouse_id: int | None = None,
) -> dict[str, int]:
    qty_int = _validate_qty(qty)
    product = await _get_product(db, tenant_id=tenant_id, product_id=product_id)
    if warehouse_id is None:
        warehouse = await _get_default_warehouse(db, tenant_id=tenant_id)
    else:
        warehouse = await _get_warehouse(db, tenant_id=tenant_id, warehouse_id=warehouse_id)

    stock = await _get_or_create_stock(db, product=product, warehouse=warehouse)
    if int(stock.reserved_quantity) < qty_int or int(stock.quantity) < qty_int:
        raise SmartSellValidationError("Insufficient stock", "INSUFFICIENT_STOCK", http_status=422)

    prev_qty = int(stock.quantity)
    stock.reserved_quantity = int(stock.reserved_quantity) - qty_int
    stock.quantity = prev_qty - qty_int

    movement = StockMovement(
        stock_id=stock.id,
        product_id=product.id,
        movement_type=MovementType.FULFILL.value,
        quantity=-abs(qty_int),
        previous_quantity=prev_qty,
        new_quantity=int(stock.quantity),
        reference_type=reference_type,
        reference_id=reference_id,
        reason="fulfill",
    )
    db.add(movement)
    await db.flush()

    return _build_result(stock)
