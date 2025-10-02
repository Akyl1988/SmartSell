# app/api/v1/warehouses.py
from __future__ import annotations

"""
Warehouses router (enterprise-grade):
- Company scoping и soft-delete.
- RBAC/Scopes: кладовщик/администратор + опциональные скоупы.
- Rate limit (API/auth профили) и идемпотентность для мутаций (POST/PUT/PATCH).
- Транзакционная передача остатков c row-level locking (FOR UPDATE).
- Безопасные обновления (exclude_unset), проверка на дубли, аккуратные ответы/ошибки.
- Аудит действий.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

# --- core deps / db / security / logging ---
try:
    from app.core.database import get_db  # предпочтительно
except Exception:  # pragma: no cover
    from app.core.db import get_db  # fallback

from app.core.deps import (
    api_rate_limit_dep,
    ensure_idempotency,
    set_idempotency_result,
    get_pagination,
    Pagination,
    get_client_info,
    # если используете скоупы — раскомментируйте и добавьте к dependencies роутов
    # require_scopes,
)
from app.core.exceptions import bad_request, not_found, conflict
from app.core.logging import audit_logger
from app.core.security import get_current_user, require_storekeeper  # ваш существующий чек

# --- models & schemas ---
from app.models import Product, ProductStock, StockMovement, User, Warehouse
from app.schemas import (
    ProductStockCreate,
    ProductStockResponse,
    StockTransfer,
    WarehouseCreate,
    WarehouseResponse,
    WarehouseStats,
    WarehouseUpdate,
)

router = APIRouter(
    prefix="/warehouses",
    tags=["warehouses"],
    dependencies=[Depends(api_rate_limit_dep)],
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _company_filter(model, company_id: int):
    """Удобный фильтр по компании с учётом soft-delete (если поле есть)."""
    cond = [model.company_id == company_id]
    if hasattr(model, "is_deleted"):
        cond.append(model.is_deleted.is_(False))
    return and_(*cond)


async def _ensure_warehouse_belongs(
    db: AsyncSession, warehouse_id: int, company_id: int, load_for_update: bool = False
) -> Warehouse | None:
    q = (
        select(Warehouse)
        .where(
            and_(
                Warehouse.id == warehouse_id,
                Warehouse.company_id == company_id,
                Warehouse.is_deleted.is_(False),
            )
        )
        .options(selectinload(Warehouse.company))
    )
    if load_for_update:
        q = q.with_for_update()
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def _ensure_product_belongs(
    db: AsyncSession, product_id: int, company_id: int
) -> Product | None:
    res = await db.execute(
        select(Product).where(
            and_(Product.id == product_id, Product.company_id == company_id)
        )
    )
    return res.scalar_one_or_none()


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------

@router.get(
    "",
    response_model=list[WarehouseResponse],
    summary="Список складов компании (пагинация, сортировка)",
)
async def get_warehouses(
    p: Pagination = Depends(get_pagination),
    q: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Warehouse)
        .where(
            and_(
                Warehouse.company_id == current_user.company_id,
                Warehouse.is_deleted.is_(False),
            )
        )
        .order_by(Warehouse.is_main.desc(), Warehouse.name.asc())
        .offset(p.offset)
        .limit(p.limit)
    )
    if q:
        stmt = stmt.where(Warehouse.name.ilike(f"%{q}%"))
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post(
    "",
    response_model=WarehouseResponse,
    summary="Создать склад",
    dependencies=[Depends(require_storekeeper), Depends(ensure_idempotency)],
)
async def create_warehouse(
    warehouse_data: WarehouseCreate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Проверка дубля по названию в рамках компании (если это ваш бизнес-инвариант)
    exists = await db.execute(
        select(func.count(Warehouse.id)).where(
            and_(
                Warehouse.company_id == current_user.company_id,
                Warehouse.name == warehouse_data.name,
                Warehouse.is_deleted.is_(False),
            )
        )
    )
    if exists.scalar_one() > 0:
        raise conflict("Warehouse with this name already exists in your company")

    # Если помечаем как главный — снимаем флаг у остальных (bulk update)
    if warehouse_data.is_main:
        await db.execute(
            update(Warehouse)
            .where(
                and_(
                    Warehouse.company_id == current_user.company_id,
                    Warehouse.is_main.is_(True),
                    Warehouse.is_deleted.is_(False),
                )
            )
            .values(is_main=False)
        )

    # Создание
    warehouse = Warehouse(company_id=current_user.company_id, **warehouse_data.model_dump())
    db.add(warehouse)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # Если у вас есть уникальные индексы — аккуратно возвращаем 409
        raise conflict("Warehouse violates a unique constraint") from e

    await db.refresh(warehouse)

    # Аудит
    client = get_client_info(request)
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="warehouse_create",
        resource_type="warehouse",
        resource_id=str(warehouse.id),
        changes=warehouse_data.model_dump(),
    )

    # Идемпотентность: фиксация результата
    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_201_CREATED)

    response.status_code = status.HTTP_201_CREATED
    return warehouse


@router.get(
    "/{warehouse_id}",
    response_model=WarehouseResponse,
    summary="Получить склад по ID",
)
async def get_warehouse(
    warehouse_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    warehouse = await _ensure_warehouse_belongs(db, warehouse_id, current_user.company_id)
    if not warehouse:
        raise not_found("Warehouse not found")
    return warehouse


@router.put(
    "/{warehouse_id}",
    response_model=WarehouseResponse,
    summary="Обновить склад",
    dependencies=[Depends(require_storekeeper), Depends(ensure_idempotency)],
)
async def update_warehouse(
    warehouse_id: int,
    warehouse_data: WarehouseUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    warehouse = await _ensure_warehouse_belongs(db, warehouse_id, current_user.company_id)
    if not warehouse:
        raise not_found("Warehouse not found")

    # Если ставим главным — снять флаг у остальных
    if warehouse_data.is_main and not warehouse.is_main:
        await db.execute(
            update(Warehouse)
            .where(
                and_(
                    Warehouse.company_id == current_user.company_id,
                    Warehouse.is_main.is_(True),
                    Warehouse.id != warehouse_id,
                    Warehouse.is_deleted.is_(False),
                )
            )
            .values(is_main=False)
        )

    # Обновление только присланных полей
    updates = warehouse_data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(warehouse, field, value)

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise conflict("Warehouse update violates a constraint") from e

    await db.refresh(warehouse)

    # Аудит
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="warehouse_update",
        resource_type="warehouse",
        resource_id=str(warehouse.id),
        changes=updates,
    )

    # Идемпотентность
    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

    return warehouse


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_200_OK,
    summary="Удалить склад (soft delete, если нет остатков)",
    dependencies=[Depends(require_storekeeper), Depends(ensure_idempotency)],
)
async def delete_warehouse(
    warehouse_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    warehouse = await _ensure_warehouse_belongs(db, warehouse_id, current_user.company_id)
    if not warehouse:
        raise not_found("Warehouse not found")

    # Проверяем остатки > 0
    has_stock = await db.execute(
        select(func.count(ProductStock.id)).where(
            and_(ProductStock.warehouse_id == warehouse_id, ProductStock.quantity > 0)
        )
    )
    if has_stock.scalar_one() > 0:
        raise bad_request("Cannot delete warehouse with existing stock")

    # Soft delete (предполагаем метод soft_delete() в модели)
    if hasattr(warehouse, "soft_delete"):
        warehouse.soft_delete()
    else:
        warehouse.is_deleted = True  # fallback

    await db.commit()

    # Аудит
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="warehouse_delete",
        resource_type="warehouse",
        resource_id=str(warehouse.id),
        changes={"deleted": True},
    )

    # Идемпотентность
    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

    return {"message": "Warehouse deleted successfully"}


@router.get(
    "/{warehouse_id}/stocks",
    response_model=list[ProductStockResponse],
    summary="Остатки по складу",
)
async def get_warehouse_stocks(
    warehouse_id: int,
    p: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wh = await _ensure_warehouse_belongs(db, warehouse_id, current_user.company_id)
    if not wh:
        raise not_found("Warehouse not found")

    stmt = (
        select(ProductStock)
        .options(
            selectinload(ProductStock.product),
            selectinload(ProductStock.warehouse),
        )
        .where(ProductStock.warehouse_id == warehouse_id)
        .order_by(ProductStock.quantity.desc(), ProductStock.id.asc())
        .offset(p.offset)
        .limit(p.limit)
    )
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post(
    "/stocks",
    response_model=ProductStockResponse,
    summary="Создать/обновить остаток товара на складе",
    dependencies=[Depends(require_storekeeper), Depends(ensure_idempotency)],
)
async def create_or_update_product_stock(
    stock_data: ProductStockCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Проверка принадлежности продукта и склада компании
    product = await _ensure_product_belongs(db, stock_data.product_id, current_user.company_id)
    if not product:
        raise not_found("Product not found")

    wh = await _ensure_warehouse_belongs(db, stock_data.warehouse_id, current_user.company_id)
    if not wh:
        raise not_found("Warehouse not found")

    # Ищем текущую запись
    res = await db.execute(
        select(ProductStock).where(
            and_(
                ProductStock.product_id == stock_data.product_id,
                ProductStock.warehouse_id == stock_data.warehouse_id,
            )
        )
    )
    existing = res.scalar_one_or_none()

    previous_qty = existing.quantity if existing else 0

    if existing:
        # Обновляем
        existing.quantity = stock_data.quantity
        existing.min_quantity = stock_data.min_quantity
        existing.max_quantity = stock_data.max_quantity
        existing.location = stock_data.location
        stock = existing
    else:
        # Создаём
        stock = ProductStock(**stock_data.model_dump())
        db.add(stock)
        await db.flush()  # чтобы получить stock.id

    # Движение
    movement = StockMovement(
        stock_id=stock.id,
        movement_type="adjustment",
        quantity=stock_data.quantity - previous_qty,
        previous_quantity=previous_qty,
        new_quantity=stock_data.quantity,
        reason="Stock initialization" if not existing else "Stock adjustment",
        user_id=current_user.id,
    )
    db.add(movement)

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise conflict("Stock change violates a constraint") from e

    await db.refresh(stock)

    # Аудит
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="stock_adjustment",
        resource_type="product_stock",
        resource_id=str(stock.id),
        changes=stock_data.model_dump(),
    )

    # Идемпотентность
    if hasattr(request.state, "idempotency_key"):
        # Возвращаем 200 для update, 201 для создания — можно различать по existing
        code = status.HTTP_201_CREATED if not existing else status.HTTP_200_OK
        await set_idempotency_result(request.state.idempotency_key, code)

    return stock


@router.post(
    "/transfer",
    summary="Перевод остатков между складами (транзакционно)",
    dependencies=[Depends(require_storekeeper), Depends(ensure_idempotency)],
)
async def transfer_stock(
    transfer_data: StockTransfer,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if transfer_data.from_warehouse_id == transfer_data.to_warehouse_id:
        raise bad_request("Source and destination warehouses must be different")

    # Проверка принадлежности складов компании
    src_wh = await _ensure_warehouse_belongs(
        db, transfer_data.from_warehouse_id, current_user.company_id
    )
    if not src_wh:
        raise not_found("Source warehouse not found")
    dst_wh = await _ensure_warehouse_belongs(
        db, transfer_data.to_warehouse_id, current_user.company_id
    )
    if not dst_wh:
        raise not_found("Destination warehouse not found")

    # Проверка продукта
    product = await _ensure_product_belongs(db, transfer_data.product_id, current_user.company_id)
    if not product:
        raise not_found("Product not found")

    # Транзакция и row-level lock для обеих записей остатков
    # Чтобы избежать дедлока — блокируем в стабильном порядке (по warehouse_id)
    wid_a, wid_b = sorted([transfer_data.from_warehouse_id, transfer_data.to_warehouse_id])
    async with db.begin():
        # Читаем и блокируем остатки
        res_a = await db.execute(
            select(ProductStock)
            .where(
                and_(
                    ProductStock.product_id == transfer_data.product_id,
                    ProductStock.warehouse_id == wid_a,
                )
            )
            .with_for_update()
            .options(joinedload(ProductStock.warehouse))
        )
        stock_a = res_a.scalar_one_or_none()

        res_b = await db.execute(
            select(ProductStock)
            .where(
                and_(
                    ProductStock.product_id == transfer_data.product_id,
                    ProductStock.warehouse_id == wid_b,
                )
            )
            .with_for_update()
            .options(joinedload(ProductStock.warehouse))
        )
        stock_b = res_b.scalar_one_or_none()

        # Мэппим обратно на source/dest
        source_stock = stock_a if wid_a == transfer_data.from_warehouse_id else stock_b
        dest_stock = stock_b if wid_b == transfer_data.to_warehouse_id else stock_a

        if not source_stock:
            raise not_found("Source stock not found")

        # Защита от отрицательного остатка
        # (если у вас есть reserved/allocated — замените на available_quantity)
        if (source_stock.available_quantity if hasattr(source_stock, "available_quantity") else source_stock.quantity) < transfer_data.quantity:
            raise bad_request("Insufficient stock for transfer")

        # Создаём запись назначения, если нет
        if not dest_stock:
            dest_stock = ProductStock(
                product_id=transfer_data.product_id,
                warehouse_id=transfer_data.to_warehouse_id,
                quantity=0,
            )
            db.add(dest_stock)
            await db.flush()

        # Сохраняем прошлые значения
        source_prev_qty = source_stock.quantity
        dest_prev_qty = dest_stock.quantity

        # Изменяем остатки
        source_stock.quantity = source_prev_qty - transfer_data.quantity
        dest_stock.quantity = dest_prev_qty + transfer_data.quantity

        # Движения
        out_movement = StockMovement(
            stock_id=source_stock.id,
            movement_type="out",
            quantity=transfer_data.quantity,  # положительное значение; трактуем по типу
            previous_quantity=source_prev_qty,
            new_quantity=source_stock.quantity,
            reference_type="transfer",
            reference_id=dest_stock.id,
            reason=transfer_data.reason,
            notes=transfer_data.notes,
            user_id=current_user.id,
        )
        in_movement = StockMovement(
            stock_id=dest_stock.id,
            movement_type="in",
            quantity=transfer_data.quantity,
            previous_quantity=dest_prev_qty,
            new_quantity=dest_stock.quantity,
            reference_type="transfer",
            reference_id=source_stock.id,
            reason=transfer_data.reason,
            notes=transfer_data.notes,
            user_id=current_user.id,
        )
        db.add(out_movement)
        db.add(in_movement)

    # Аудит (после успешного коммита контекста)
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="stock_transfer",
        resource_type="product_stock",
        resource_id=str(source_stock.id),  # типо-идентификатор операции
        changes={
            "from_warehouse": transfer_data.from_warehouse_id,
            "to_warehouse": transfer_data.to_warehouse_id,
            "quantity": transfer_data.quantity,
            "product_id": transfer_data.product_id,
        },
    )

    # Идемпотентность
    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

    return {"message": "Stock transferred successfully"}


@router.get(
    "/{warehouse_id}/stats",
    response_model=WarehouseStats,
    summary="Статистика по складу",
)
async def get_warehouse_stats(
    warehouse_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wh = await _ensure_warehouse_belongs(db, warehouse_id, current_user.company_id)
    if not wh:
        raise not_found("Warehouse not found")

    res = await db.execute(
        select(ProductStock)
        .options(selectinload(ProductStock.product))
        .where(ProductStock.warehouse_id == warehouse_id)
    )
    stocks = res.scalars().all()

    total_products = len(stocks)
    total_stock = sum(s.quantity for s in stocks)
    low_stock_products = sum(1 for s in stocks if getattr(s, "is_low_stock", False))
    out_of_stock_products = sum(1 for s in stocks if s.quantity == 0)

    total_value = 0.0
    for s in stocks:
        cost = float(getattr(s.product, "cost_price", 0) or 0)
        total_value += s.quantity * cost

    return WarehouseStats(
        total_products=total_products,
        total_stock=total_stock,
        low_stock_products=low_stock_products,
        out_of_stock_products=out_of_stock_products,
        total_value=total_value,
    )
