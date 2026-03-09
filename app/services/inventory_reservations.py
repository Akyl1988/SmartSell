"""Inventory reservation service (async, tenant-safe)."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.models.product import Product
from app.models.warehouse import MovementType, ProductStock, StockMovement, Warehouse
from app.services.preorder_policy import evaluate_preorder_state


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
    base = (
        select(Warehouse)
        .where(
            Warehouse.company_id == tenant_id,
            Warehouse.is_active.is_(True),
            Warehouse.is_archived.is_(False),
        )
        .order_by(Warehouse.id.asc())
    )
    result = await db.execute(base.where(Warehouse.is_main.is_(True)))
    warehouse = result.scalars().first()
    if not warehouse:
        result = await db.execute(base)
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

    stock = ProductStock(
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity=0,
        reserved_quantity=0,
    )
    db.add(stock)
    await db.flush()
    return stock


async def _find_movement_stock(
    db: AsyncSession,
    *,
    tenant_id: int,
    product_id: int,
    movement_type: str,
    reference_type: str,
    reference_id: int,
    warehouse_id: int | None = None,
) -> ProductStock | None:
    stmt = (
        select(ProductStock)
        .join(StockMovement, StockMovement.stock_id == ProductStock.id)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(
            StockMovement.movement_type == movement_type,
            StockMovement.reference_type == reference_type,
            StockMovement.reference_id == reference_id,
            StockMovement.product_id == product_id,
            Warehouse.company_id == tenant_id,
        )
        .order_by(StockMovement.id.desc())
        .limit(1)
    )
    if warehouse_id is not None:
        stmt = stmt.where(ProductStock.warehouse_id == warehouse_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _build_result(stock: ProductStock) -> dict[str, int]:
    return {
        "product_id": int(stock.product_id),
        "warehouse_id": int(stock.warehouse_id),
        "on_hand": int(stock.quantity),
        "reserved": int(stock.reserved_quantity),
        "available": int(stock.available_quantity),
    }


def _assert_stock_invariants(stock: ProductStock) -> None:
    qty = int(stock.quantity or 0)
    reserved = int(stock.reserved_quantity or 0)
    if qty < 0:
        raise SmartSellValidationError(
            "Stock quantity cannot be negative",
            "NEGATIVE_STOCK",
            http_status=422,
        )
    if reserved < 0:
        raise SmartSellValidationError(
            "Reserved quantity cannot be negative",
            "NEGATIVE_RESERVED_STOCK",
            http_status=422,
        )
    if reserved > qty:
        raise SmartSellValidationError(
            "Reserved quantity cannot exceed stock quantity",
            "RESERVED_EXCEEDS_STOCK",
            http_status=422,
        )


class ReservationResult(BaseModel):
    ok: bool
    error_code: str | None = None
    product_id: int | None = None
    warehouse_id: int | None = None
    on_hand: int | None = None
    reserved: int | None = None
    available: int | None = None


async def _resolve_preorder_warehouse_id(
    db: AsyncSession,
    *,
    tenant_id: int,
    preorder_id: int,
    product_id: int,
) -> int | None:
    result = await db.execute(
        select(ProductStock.warehouse_id)
        .join(StockMovement, StockMovement.stock_id == ProductStock.id)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(
            StockMovement.reference_type == "preorder",
            StockMovement.reference_id == preorder_id,
            StockMovement.product_id == product_id,
            StockMovement.movement_type == MovementType.RESERVE.value,
            Warehouse.company_id == tenant_id,
        )
        .order_by(StockMovement.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def reserve_stock_for_preorder(
    *,
    db: AsyncSession,
    company_id: int,
    product_id: int,
    quantity: int,
    preorder_id: int,
    user_id: int | None = None,
) -> ReservationResult:
    try:
        result = await reserve_and_log(
            db,
            tenant_id=company_id,
            product_id=product_id,
            qty=int(quantity),
            reference_type="preorder",
            reference_id=preorder_id,
        )
    except SmartSellValidationError as exc:
        if exc.code in {"WAREHOUSE_NOT_CONFIGURED", "INSUFFICIENT_STOCK"}:
            return ReservationResult(ok=False, error_code=exc.code)
        raise

    return ReservationResult(
        ok=True,
        product_id=result.get("product_id"),
        warehouse_id=result.get("warehouse_id"),
        on_hand=result.get("on_hand"),
        reserved=result.get("reserved"),
        available=result.get("available"),
    )


async def release_stock_for_preorder(
    *,
    db: AsyncSession,
    company_id: int,
    product_id: int,
    quantity: int,
    preorder_id: int,
    user_id: int | None = None,
) -> None:
    warehouse_id = await _resolve_preorder_warehouse_id(
        db,
        tenant_id=company_id,
        preorder_id=preorder_id,
        product_id=product_id,
    )
    if warehouse_id is None:
        raise SmartSellValidationError(
            "Reservation not found",
            "RESERVATION_NOT_FOUND",
            http_status=409,
        )

    await release_and_log(
        db,
        tenant_id=company_id,
        product_id=product_id,
        qty=int(quantity),
        reference_type="preorder",
        reference_id=preorder_id,
        warehouse_id=warehouse_id,
    )


async def fulfill_preorder_reservation(
    *,
    db: AsyncSession,
    company_id: int,
    product_id: int,
    quantity: int,
    preorder_id: int,
    user_id: int | None = None,
) -> None:
    warehouse_id = await _resolve_preorder_warehouse_id(
        db,
        tenant_id=company_id,
        preorder_id=preorder_id,
        product_id=product_id,
    )
    if warehouse_id is None:
        raise SmartSellValidationError(
            "Reservation not found",
            "RESERVATION_NOT_FOUND",
            http_status=409,
        )
    await fulfill_reservation(
        db,
        tenant_id=company_id,
        product_id=product_id,
        qty=int(quantity),
        reference_type="preorder",
        reference_id=preorder_id,
        warehouse_id=warehouse_id,
    )


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
    existing = await _find_movement_stock(
        db,
        tenant_id=tenant_id,
        product_id=product_id,
        movement_type=MovementType.RESERVE.value,
        reference_type=reference_type,
        reference_id=reference_id,
        warehouse_id=warehouse_id,
    )
    if existing is not None:
        return _build_result(existing)
    product = await _get_product(db, tenant_id=tenant_id, product_id=product_id)
    if warehouse_id is None:
        warehouse = await _get_default_warehouse(db, tenant_id=tenant_id)
    else:
        warehouse = await _get_warehouse(db, tenant_id=tenant_id, warehouse_id=warehouse_id)

    stock = await _get_or_create_stock(db, product=product, warehouse=warehouse)
    _assert_stock_invariants(stock)
    if stock.available_quantity < qty_int:
        raise SmartSellValidationError("Insufficient stock", "INSUFFICIENT_STOCK", http_status=422)

    stock.reserved_quantity = int(stock.reserved_quantity) + qty_int
    _assert_stock_invariants(stock)

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

    await evaluate_preorder_state(db, company_id=tenant_id, product_id=product.id)

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
    fulfill = await _find_movement_stock(
        db,
        tenant_id=tenant_id,
        product_id=product_id,
        movement_type=MovementType.FULFILL.value,
        reference_type=reference_type,
        reference_id=reference_id,
        warehouse_id=warehouse_id,
    )
    if fulfill is not None:
        raise SmartSellValidationError(
            "Reservation already fulfilled",
            "RESERVATION_ALREADY_FULFILLED",
            http_status=409,
        )
    existing = await _find_movement_stock(
        db,
        tenant_id=tenant_id,
        product_id=product_id,
        movement_type=MovementType.RELEASE.value,
        reference_type=reference_type,
        reference_id=reference_id,
        warehouse_id=warehouse_id,
    )
    if existing is not None:
        return _build_result(existing)
    product = await _get_product(db, tenant_id=tenant_id, product_id=product_id)
    if warehouse_id is None:
        warehouse = await _get_default_warehouse(db, tenant_id=tenant_id)
    else:
        warehouse = await _get_warehouse(db, tenant_id=tenant_id, warehouse_id=warehouse_id)

    stock = await _get_or_create_stock(db, product=product, warehouse=warehouse)
    _assert_stock_invariants(stock)
    if int(stock.reserved_quantity) < qty_int:
        raise SmartSellValidationError("Insufficient reserved stock", "INVALID_RELEASE", http_status=422)

    stock.reserved_quantity = int(stock.reserved_quantity) - qty_int
    _assert_stock_invariants(stock)

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

    await evaluate_preorder_state(db, company_id=tenant_id, product_id=product.id)

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
    existing = await _find_movement_stock(
        db,
        tenant_id=tenant_id,
        product_id=product_id,
        movement_type=MovementType.FULFILL.value,
        reference_type=reference_type,
        reference_id=reference_id,
        warehouse_id=warehouse_id,
    )
    if existing is not None:
        raise SmartSellValidationError(
            "Reservation already fulfilled",
            "RESERVATION_ALREADY_FULFILLED",
            http_status=409,
        )
    product = await _get_product(db, tenant_id=tenant_id, product_id=product_id)
    if warehouse_id is None:
        warehouse = await _get_default_warehouse(db, tenant_id=tenant_id)
    else:
        warehouse = await _get_warehouse(db, tenant_id=tenant_id, warehouse_id=warehouse_id)

    stock = await _get_or_create_stock(db, product=product, warehouse=warehouse)
    _assert_stock_invariants(stock)
    if int(stock.reserved_quantity) < qty_int or int(stock.quantity) < qty_int:
        raise SmartSellValidationError("Insufficient stock", "INSUFFICIENT_STOCK", http_status=422)

    prev_qty = int(stock.quantity)
    stock.reserved_quantity = int(stock.reserved_quantity) - qty_int
    stock.quantity = prev_qty - qty_int
    _assert_stock_invariants(stock)

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

    await evaluate_preorder_state(db, company_id=tenant_id, product_id=product.id)

    return _build_result(stock)
