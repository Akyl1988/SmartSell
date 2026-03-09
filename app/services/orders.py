"""Service layer for order lifecycle (Phase 1 minimal flow)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, selectinload

from app.core.exceptions import AuthorizationError, NotFoundError, SmartSellValidationError
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.warehouse import MovementType, ProductStock, StockMovement, Warehouse
from app.schemas.order import OrderCreate
from app.services.inventory_reservations import fulfill_reservation, release_and_log, reserve_and_log


async def _get_product(db: AsyncSession, *, company_id: int, product_id: int) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id, Product.company_id == company_id))
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    return product


async def _get_order_for_update(db: AsyncSession, *, company_id: int, order_id: int) -> Order:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), lazyload(Order.invoice))
        .with_for_update()
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError("Order not found", "ORDER_NOT_FOUND")
    if order.company_id != company_id:
        raise AuthorizationError("Forbidden", "FORBIDDEN")
    return order


async def _aggregate_items(db: AsyncSession, *, order_id: int) -> dict[int, int]:
    aggregated: dict[int, int] = {}
    result = await db.execute(select(OrderItem.product_id, OrderItem.quantity).where(OrderItem.order_id == order_id))
    for product_id, quantity in result.all():
        if not product_id:
            continue
        aggregated[int(product_id)] = aggregated.get(int(product_id), 0) + int(quantity or 0)
    return aggregated


async def _resolve_order_warehouse_id(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int,
    product_id: int,
) -> int | None:
    result = await db.execute(
        select(ProductStock.warehouse_id)
        .join(StockMovement, StockMovement.stock_id == ProductStock.id)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(
            StockMovement.reference_type == "order",
            StockMovement.reference_id == order_id,
            StockMovement.product_id == product_id,
            StockMovement.movement_type == MovementType.RESERVE.value,
            Warehouse.company_id == company_id,
        )
        .order_by(StockMovement.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_order(
    db: AsyncSession,
    *,
    company_id: int,
    created_by_user_id: int | None,
    payload: OrderCreate,
) -> Order:
    order = Order(
        company_id=company_id,
        order_number=f"ORD-{uuid4().hex[:10]}",
        external_id=payload.external_id,
        source=payload.source,
        status=OrderStatus.PENDING,
        customer_phone=payload.customer_phone,
        customer_email=payload.customer_email,
        customer_name=payload.customer_name,
        customer_address=payload.customer_address,
        delivery_method=payload.delivery_method,
        delivery_address=payload.delivery_address,
        delivery_date=payload.delivery_date,
        delivery_time=payload.delivery_time,
        notes=payload.notes,
        currency="KZT",
    )
    db.add(order)
    await db.flush()

    items: list[OrderItem] = []
    for item in payload.items:
        if item.product_id is not None:
            await _get_product(db, company_id=company_id, product_id=int(item.product_id))
        order_item = OrderItem(
            order_id=order.id,
            product_id=item.product_id,
            sku=item.sku,
            name=item.name,
            unit_price=Decimal(item.unit_price),
            quantity=int(item.quantity),
            total_price=Decimal("0"),
            cost_price=Decimal("0"),
            notes=item.notes,
        )
        order_item.calculate_total()
        items.append(order_item)

    if items:
        db.add_all(items)

    subtotal = sum((item.total_price for item in items), Decimal("0"))
    order.subtotal = subtotal
    order.total_amount = (
        subtotal
        + Decimal(order.tax_amount or 0)
        + Decimal(order.shipping_amount or 0)
        - Decimal(order.discount_amount or 0)
    ).quantize(Decimal("0.01"))
    await db.commit()
    result = await db.execute(select(Order).where(Order.id == order.id).options(selectinload(Order.items)))
    return result.scalar_one()


async def confirm_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int,
    user_id: int | None,
) -> Order:
    order = await _get_order_for_update(db, company_id=company_id, order_id=order_id)

    if order.status == OrderStatus.CONFIRMED:
        return order

    if order.status != OrderStatus.PENDING:
        raise SmartSellValidationError(
            "Invalid order status transition",
            "INVALID_ORDER_STATUS",
            http_status=409,
        )

    for product_id, qty in (await _aggregate_items(db, order_id=order.id)).items():
        await reserve_and_log(
            db,
            tenant_id=company_id,
            product_id=product_id,
            qty=qty,
            reference_type="order",
            reference_id=order.id,
        )

    order.confirm(user_id=user_id, note="order_confirmed", session=db)
    await db.commit()
    await db.refresh(order)
    return order


async def cancel_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int,
    user_id: int | None,
) -> Order:
    order = await _get_order_for_update(db, company_id=company_id, order_id=order_id)

    if order.status == OrderStatus.CANCELLED:
        return order

    if order.status not in {OrderStatus.PENDING, OrderStatus.CONFIRMED}:
        raise SmartSellValidationError(
            "Invalid order status transition",
            "INVALID_ORDER_STATUS",
            http_status=409,
        )

    if order.status == OrderStatus.CONFIRMED:
        for product_id, qty in (await _aggregate_items(db, order_id=order.id)).items():
            await release_and_log(
                db,
                tenant_id=company_id,
                product_id=product_id,
                qty=qty,
                reference_type="order",
                reference_id=order.id,
            )

    order.cancel(user_id=user_id, note="order_cancelled", session=db)
    await db.commit()
    await db.refresh(order)
    return order


async def ship_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int,
    user_id: int | None,
) -> Order:
    order = await _get_order_for_update(db, company_id=company_id, order_id=order_id)

    if order.status == OrderStatus.SHIPPED:
        return order

    if order.status != OrderStatus.CONFIRMED:
        raise SmartSellValidationError(
            "Invalid order status transition",
            "INVALID_ORDER_STATUS",
            http_status=409,
        )

    order.ship(user_id=user_id, note="order_shipped", session=db)
    await db.commit()
    await db.refresh(order)
    return order


async def fulfill_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int,
    user_id: int | None,
) -> Order:
    order = await _get_order_for_update(db, company_id=company_id, order_id=order_id)

    if order.status == OrderStatus.COMPLETED:
        raise SmartSellValidationError(
            "Order already fulfilled",
            "ORDER_ALREADY_FULFILLED",
            http_status=409,
        )

    if order.status not in {OrderStatus.CONFIRMED, OrderStatus.SHIPPED}:
        raise SmartSellValidationError(
            "Invalid order status transition",
            "INVALID_ORDER_STATUS",
            http_status=409,
        )

    for product_id, qty in (await _aggregate_items(db, order_id=order.id)).items():
        warehouse_id = await _resolve_order_warehouse_id(
            db,
            company_id=company_id,
            order_id=order.id,
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
            qty=qty,
            reference_type="order",
            reference_id=order.id,
            warehouse_id=warehouse_id,
        )

    order.complete(user_id=user_id, note="order_completed", session=db)
    await db.commit()
    await db.refresh(order)
    return order
