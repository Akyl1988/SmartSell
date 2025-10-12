# app/routers/orders.py
from __future__ import annotations

"""
Orders router for order management (enterprise-grade).
"""

from typing import Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

# DB session (учитываем ваш возможный merge db/database)
try:
    from app.core.database import get_db  # type: ignore
except Exception:
    from app.core.db import get_db  # fallback

from app.core.deps import api_rate_limit_dep, ensure_idempotency
from app.core.errors import bad_request, conflict, not_found, server_error
from app.core.logging import audit_logger
from app.core.security import get_current_user, require_manager
from app.models import Order, OrderItem, Product, ProductStock, User
from app.schemas import (
    OrderCreate,
    OrderFilter,
    OrderResponse,
    OrderStatusUpdate,
    OrderUpdate,
    PaginationParams,
)
from app.services.email_service import EmailService
from app.services.kaspi_service import KaspiService
from app.utils.pdf import generate_invoice_pdf

router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(api_rate_limit_dep)],
)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

_ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"placed", "cancelled"},
    "placed": {"confirmed", "cancelled"},
    "confirmed": {"packing", "cancelled"},
    "packing": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
    "paid": {"confirmed", "packing", "cancelled"},  # если оплата идёт раньше подтверждения
}


def _company_scope(company_id: int):
    return and_(Order.company_id == company_id, Order.is_deleted.is_(False))


def _product_company_scope(company_id: int):
    return and_(Product.company_id == company_id, Product.is_deleted.is_(False))


def _ensure_transition(old: str, new: str) -> None:
    if old is None:
        # Разрешаем выставлять первый статус
        return
    allowed = _ALLOWED_STATUS_TRANSITIONS.get(old, set())
    if new not in allowed:
        raise conflict(f"Illegal status transition: {old} → {new}")


async def _load_order(
    db: AsyncSession, company_id: int, order_id: int, with_items: bool = True
) -> Optional[Order]:
    stmt: Select = (
        select(Order)
        .where(and_(Order.id == order_id, _company_scope(company_id)))
        .order_by(Order.id.desc())
    )
    if with_items:
        stmt = stmt.options(selectinload(Order.items))
    return (await db.execute(stmt)).scalar_one_or_none()


async def _reserve_product_stock(
    db: AsyncSession,
    product_id: int,
    quantity: int,
) -> None:
    """
    Резервирование склада для товара с конкурентной безопасностью:
    - Блокируем строки стока SKIP LOCKED
    - Резервируем по складам, пока не наберём количество
    """
    remaining = quantity
    # Загружаем склады по этому товару с блокировкой
    stocks = (
        (
            await db.execute(
                select(ProductStock)
                .where(ProductStock.product_id == product_id)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    for stock in stocks:
        if remaining <= 0:
            break
        available = stock.available_quantity
        if available <= 0:
            continue
        to_reserve = min(available, remaining)
        stock.reserve(to_reserve)  # предполагается, что модель инкрементит reserved_quantity
        remaining -= to_reserve

    if remaining > 0:
        raise bad_request(f"Insufficient stock for product ID {product_id}")


async def _release_product_stock(
    db: AsyncSession,
    product_id: int,
    quantity: int,
) -> None:
    """
    Освобождение резерва (например, при отмене).
    Снимаем резерв в обратном порядке (или равномерно) — здесь берём FIFO.
    """
    remaining = quantity
    stocks = (
        (
            await db.execute(
                select(ProductStock)
                .where(ProductStock.product_id == product_id)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    for stock in stocks:
        if remaining <= 0:
            break
        reserved = stock.reserved_quantity
        if reserved <= 0:
            continue
        to_release = min(reserved, remaining)
        stock.release(to_release)  # предполагается метод модели, уменьшающий reserved_quantity
        remaining -= to_release


# -------------------------------------------------------------------
# GET /orders
# -------------------------------------------------------------------


@router.get("/", response_model=list[OrderResponse], summary="Список заказов")
async def get_orders(
    pagination: PaginationParams = Depends(),
    filter_params: OrderFilter = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Order)
        .options(selectinload(Order.items))
        .where(_company_scope(current_user.company_id))
    )

    if filter_params.search:
        s = f"%{filter_params.search}%"
        query = query.where(
            func.lower(Order.order_number).ilike(func.lower(s))
            | func.lower(Order.customer_name).ilike(func.lower(s))
            | func.lower(Order.customer_phone).ilike(func.lower(s))
        )

    if filter_params.status:
        query = query.where(Order.status == filter_params.status)

    if filter_params.source:
        query = query.where(Order.source == filter_params.source)

    if filter_params.customer_phone:
        query = query.where(Order.customer_phone == filter_params.customer_phone)

    if filter_params.date_from:
        query = query.where(Order.created_at >= filter_params.date_from)

    if filter_params.date_to:
        query = query.where(Order.created_at <= filter_params.date_to)

    if filter_params.min_amount:
        query = query.where(Order.total_amount >= filter_params.min_amount)

    if filter_params.max_amount:
        query = query.where(Order.total_amount <= filter_params.max_amount)

    # Сначала order_by, потом пагинация — корректный план запроса
    query = query.order_by(Order.created_at.desc(), Order.id.desc())
    query = query.offset(pagination.offset()).limit(pagination.size)

    orders = (await db.execute(query)).scalars().all()
    return orders


# -------------------------------------------------------------------
# POST /orders (idempotent)
# -------------------------------------------------------------------


@router.post(
    "/",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
    summary="Создать заказ",
)
async def create_order(
    order_data: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Генерим номер заказа
    import uuid

    order_number = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    # Создание заказа + резерв склада в транзакции
    async with db.begin():
        order = Order(
            company_id=current_user.company_id,
            order_number=order_number,
            external_id=order_data.external_id,
            source=order_data.source,
            customer_phone=order_data.customer_phone,
            customer_email=order_data.customer_email,
            customer_name=order_data.customer_name,
            customer_address=order_data.customer_address,
            delivery_method=order_data.delivery_method,
            delivery_address=order_data.delivery_address,
            delivery_date=order_data.delivery_date,
            delivery_time=order_data.delivery_time,
            notes=order_data.notes,
            status="placed",
        )
        db.add(order)
        await db.flush()  # чтобы получить order.id

        total_amount = 0.0

        for item_data in order_data.items:
            product = None
            if item_data.product_id:
                product = (
                    await db.execute(
                        select(Product).where(
                            and_(
                                Product.id == item_data.product_id,
                                _product_company_scope(current_user.company_id),
                            )
                        )
                    )
                ).scalar_one_or_none()
                if not product:
                    raise not_found(f"Product {item_data.product_id} not found")

            item = OrderItem(
                order_id=order.id,
                product_id=item_data.product_id,
                sku=item_data.sku,
                name=item_data.name,
                unit_price=item_data.unit_price,
                quantity=item_data.quantity,
                notes=item_data.notes,
            )
            item.calculate_total()
            db.add(item)
            total_amount += float(item.total_price or 0)

            # Резерв склада только если привязан продукт
            if item_data.product_id:
                await _reserve_product_stock(db, item_data.product_id, item_data.quantity)

        order.subtotal = total_amount
        order.calculate_totals()  # предполагается, что внутри выставляет total_amount и налоги/скидки

    # Отдельный commit уже сделан контекстом begin
    await db.refresh(order)

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="order_create",
        resource_type="order",
        resource_id=str(order.id),
        changes={
            "order_number": order.order_number,
            "total_amount": float(order.total_amount or 0),
        },
    )
    return order


# -------------------------------------------------------------------
# GET /orders/{id}
# -------------------------------------------------------------------


@router.get("/{order_id}", response_model=OrderResponse, summary="Получить заказ")
async def get_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, current_user.company_id, order_id, with_items=True)
    if not order:
        raise not_found("Order not found")
    return order


# -------------------------------------------------------------------
# PUT /orders/{id}
# -------------------------------------------------------------------


@router.put(
    "/{order_id}",
    response_model=OrderResponse,
    dependencies=[Depends(require_manager)],
    summary="Обновить заказ",
)
async def update_order(
    order_id: int,
    order_data: OrderUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, current_user.company_id, order_id, with_items=False)
    if not order:
        raise not_found("Order not found")

    old_values = {
        "status": order.status,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
    }

    update_data = order_data.dict(exclude_unset=True)

    async with db.begin():
        for field, value in update_data.items():
            setattr(order, field, value)

    await db.refresh(order)

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="order_update",
        resource_type="order",
        resource_id=str(order.id),
        changes={"old": old_values, "new": update_data},
    )
    return order


# -------------------------------------------------------------------
# PATCH /orders/{id}/status
# -------------------------------------------------------------------


@router.patch(
    "/{order_id}/status",
    dependencies=[Depends(require_manager)],
    summary="Обновить статус заказа",
)
async def update_order_status(
    order_id: int,
    status_data: OrderStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, current_user.company_id, order_id, with_items=False)
    if not order:
        raise not_found("Order not found")

    new_status = status_data.status
    _ensure_transition(order.status or "draft", new_status)

    async with db.begin():
        old_status = order.status
        order.status = new_status
        if status_data.notes:
            order.internal_notes = status_data.notes

        # Освобождение резерва при отмене
        if new_status == "cancelled":
            items = (
                (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id)))
                .scalars()
                .all()
            )
            for item in items:
                if item.product_id and item.quantity:
                    await _release_product_stock(db, item.product_id, item.quantity)

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="order_status_change",
        resource_type="order",
        resource_id=str(order.id),
        changes={"old_status": old_status, "new_status": new_status},
    )
    return {"message": "Order status updated successfully"}


# -------------------------------------------------------------------
# POST /orders/{id}/invoice
# -------------------------------------------------------------------


@router.post(
    "/{order_id}/invoice",
    dependencies=[Depends(require_manager)],
    summary="Сгенерировать PDF-счёт",
)
async def generate_invoice(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, current_user.company_id, order_id, with_items=True)
    if not order:
        raise not_found("Order not found")

    try:
        pdf_path = await generate_invoice_pdf(order, current_user.company)
        return {"message": "Invoice generated successfully", "pdf_url": pdf_path}
    except Exception as e:
        raise server_error(f"Failed to generate invoice: {e!s}")


# -------------------------------------------------------------------
# POST /orders/{id}/send-email
# -------------------------------------------------------------------


@router.post(
    "/{order_id}/send-email",
    dependencies=[Depends(require_manager)],
    summary="Отправить детали заказа на e-mail",
)
async def send_order_email(
    order_id: int,
    email: str,
    subject: str = "Order Details",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, current_user.company_id, order_id, with_items=True)
    if not order:
        raise not_found("Order not found")

    try:
        pdf_path = await generate_invoice_pdf(order, current_user.company)
        email_service = EmailService()
        await email_service.send_order_email(
            to_email=email, subject=subject, order=order, pdf_attachment=pdf_path
        )
        return {"message": "Email sent successfully"}
    except Exception as e:
        raise server_error(f"Failed to send email: {e!s}")


# -------------------------------------------------------------------
# POST /orders/sync-kaspi
# -------------------------------------------------------------------


@router.post(
    "/sync-kaspi",
    dependencies=[Depends(require_manager)],
    summary="Синхронизировать заказы из Kaspi",
)
async def sync_kaspi_orders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    api_key = getattr(current_user.company, "kaspi_api_key", None)
    if not api_key:
        raise bad_request("Kaspi API key not configured")

    try:
        kaspi = KaspiService(api_key)
        sync_result = await kaspi.sync_orders(current_user.company_id, db)

        audit_logger.log_data_change(
            user_id=current_user.id,
            action="kaspi_orders_sync",
            resource_type="order",
            resource_id="bulk",
            changes=sync_result,
        )
        return sync_result
    except Exception as e:
        raise server_error(f"Kaspi sync failed: {e!s}")
