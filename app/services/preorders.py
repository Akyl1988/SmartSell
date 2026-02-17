"""Service layer for store preorders."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.models.order import Order, OrderItem, OrderSource, OrderStatus
from app.models.preorder import Preorder, PreorderItem, PreorderStatus
from app.schemas.preorders import PreorderCreateIn, PreorderListFilters, PreorderUpdateIn


def _parse_dt(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except Exception as exc:
        raise SmartSellValidationError(f"{field} must be ISO 8601", "INVALID_DATE") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _calculate_total(items: list[PreorderItem]) -> Decimal | None:
    if not items:
        return None
    total = Decimal("0.00")
    for item in items:
        if item.price is None:
            continue
        total += Decimal(item.qty) * Decimal(item.price)
    return total.quantize(Decimal("0.01"))


async def create_preorder(
    db: AsyncSession,
    *,
    company_id: int,
    created_by_user_id: int | None,
    payload: PreorderCreateIn,
) -> Preorder:
    preorder = Preorder(
        company_id=company_id,
        status=PreorderStatus.NEW,
        currency=payload.currency,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        notes=payload.notes,
        created_by_user_id=created_by_user_id,
    )
    items = []
    for item in payload.items:
        items.append(
            PreorderItem(
                product_id=item.product_id,
                sku=item.sku,
                name=item.name,
                qty=int(item.qty),
                price=item.price,
            )
        )
    preorder.items = items
    preorder.total = _calculate_total(items)

    db.add(preorder)
    await db.commit()
    return await get_preorder(db, company_id=company_id, preorder_id=preorder.id)


async def list_preorders(
    db: AsyncSession,
    *,
    company_id: int,
    filters: PreorderListFilters,
    offset: int,
    limit: int,
) -> tuple[list[Preorder], int]:
    stmt = select(Preorder).where(Preorder.company_id == company_id)

    if filters.status:
        stmt = stmt.where(Preorder.status == filters.status)

    df = _parse_dt(filters.date_from, "date_from")
    dt = _parse_dt(filters.date_to, "date_to")
    if df:
        stmt = stmt.where(Preorder.created_at >= df)
    if dt:
        stmt = stmt.where(Preorder.created_at <= dt)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(Preorder.created_at.desc(), Preorder.id.desc()).offset(offset).limit(limit)
    items_stmt = items_stmt.options(selectinload(Preorder.items))
    items = (await db.execute(items_stmt)).scalars().all()

    return items, total


async def get_preorder(db: AsyncSession, *, company_id: int, preorder_id: int) -> Preorder:
    result = await db.execute(
        select(Preorder)
        .where(Preorder.id == preorder_id, Preorder.company_id == company_id)
        .options(selectinload(Preorder.items))
    )
    preorder = result.scalar_one_or_none()
    if not preorder:
        raise NotFoundError("Preorder not found", "PREORDER_NOT_FOUND")
    return preorder


async def update_preorder(
    db: AsyncSession,
    *,
    company_id: int,
    preorder_id: int,
    payload: PreorderUpdateIn,
) -> Preorder:
    preorder = await get_preorder(db, company_id=company_id, preorder_id=preorder_id)
    if preorder.status != PreorderStatus.NEW:
        raise SmartSellValidationError(
            "Preorder can only be edited in new status", "PREORDER_NOT_EDITABLE", http_status=409
        )

    data = payload.model_dump(exclude_unset=True)
    for key in ("customer_name", "customer_phone", "notes"):
        if key in data:
            setattr(preorder, key, data[key])

    if payload.items is not None:
        preorder.items.clear()
        for item in payload.items:
            preorder.items.append(
                PreorderItem(
                    product_id=item.product_id,
                    sku=item.sku,
                    name=item.name,
                    qty=int(item.qty),
                    price=item.price,
                )
            )
        preorder.total = _calculate_total(preorder.items)

    await db.commit()
    return await get_preorder(db, company_id=company_id, preorder_id=preorder.id)


def _transition(preorder: Preorder, target: PreorderStatus) -> None:
    if preorder.status == target:
        return
    if preorder.status == PreorderStatus.NEW and target == PreorderStatus.CONFIRMED:
        preorder.confirm()
        return
    if preorder.status in {PreorderStatus.NEW, PreorderStatus.CONFIRMED} and target == PreorderStatus.CANCELLED:
        preorder.cancel()
        return
    if preorder.status == PreorderStatus.CONFIRMED and target == PreorderStatus.FULFILLED:
        preorder.fulfill()
        return
    raise SmartSellValidationError("Invalid preorder status transition", "INVALID_PREORDER_STATUS", http_status=409)


async def confirm_preorder(db: AsyncSession, *, company_id: int, preorder_id: int) -> Preorder:
    preorder = await get_preorder(db, company_id=company_id, preorder_id=preorder_id)
    _transition(preorder, PreorderStatus.CONFIRMED)
    await db.commit()
    return await get_preorder(db, company_id=company_id, preorder_id=preorder.id)


async def cancel_preorder(db: AsyncSession, *, company_id: int, preorder_id: int) -> Preorder:
    preorder = await get_preorder(db, company_id=company_id, preorder_id=preorder_id)
    _transition(preorder, PreorderStatus.CANCELLED)
    await db.commit()
    return await get_preorder(db, company_id=company_id, preorder_id=preorder.id)


async def fulfill_preorder(db: AsyncSession, *, company_id: int, preorder_id: int) -> Preorder:
    nested = db.in_transaction()
    tx = db.begin_nested() if nested else db.begin()
    async with tx:
        result = await db.execute(
            select(Preorder)
            .where(Preorder.id == preorder_id, Preorder.company_id == company_id)
            .options(selectinload(Preorder.items))
            .with_for_update()
        )
        preorder = result.scalar_one_or_none()
        if not preorder:
            raise NotFoundError("Preorder not found", "PREORDER_NOT_FOUND")

        if preorder.status == PreorderStatus.FULFILLED and preorder.fulfilled_order_id:
            return preorder

        if preorder.status != PreorderStatus.CONFIRMED:
            raise SmartSellValidationError(
                "Preorder must be confirmed before fulfillment",
                "INVALID_PREORDER_STATUS",
                http_status=422,
            )

        if not preorder.items:
            raise SmartSellValidationError("Preorder has no items", "PREORDER_ITEMS_REQUIRED", http_status=422)

        for item in preorder.items:
            if item.price is None:
                raise SmartSellValidationError(
                    "Preorder item price is required",
                    "PREORDER_ITEM_PRICE_REQUIRED",
                    http_status=422,
                )

        order = Order(
            company_id=company_id,
            order_number=f"PRE-{uuid4().hex[:10]}",
            source=OrderSource.PREORDER,
            status=OrderStatus.CONFIRMED,
            currency=preorder.currency,
            customer_name=preorder.customer_name,
            customer_phone=preorder.customer_phone,
            notes=preorder.notes,
        )
        items = []
        for item in preorder.items:
            unit_price = Decimal(str(item.price))
            quantity = int(item.qty)
            total_price = (unit_price * Decimal(quantity)).quantize(Decimal("0.01"))
            items.append(
                OrderItem(
                    product_id=item.product_id,
                    sku=item.sku or "",
                    name=item.name or "",
                    unit_price=unit_price,
                    quantity=quantity,
                    total_price=total_price,
                    cost_price=Decimal("0"),
                )
            )

        order.items = items
        order.calculate_totals()
        db.add(order)
        await db.flush()
        preorder.fulfilled_order_id = order.id
        preorder.fulfilled_at = datetime.utcnow()
        _transition(preorder, PreorderStatus.FULFILLED)

    if nested:
        await db.commit()
    return await get_preorder(db, company_id=company_id, preorder_id=preorder.id)
