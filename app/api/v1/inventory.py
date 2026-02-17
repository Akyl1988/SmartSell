from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_verified_user,
    get_pagination,
    require_active_subscription,
    require_company_access,
    require_store_admin_company,
)
from app.core.exceptions import AuthorizationError, NotFoundError, SmartSellValidationError
from app.core.rbac import is_platform_admin
from app.core.security import resolve_tenant_company_id
from app.models.product import Product
from app.models.user import User
from app.models.warehouse import ProductStock, StockMovement, Warehouse
from app.schemas.base import PaginatedResponse
from app.schemas.warehouse import (
    InventoryReservationRequest,
    InventoryReservationResponse,
    ProductStockResponse,
    StockMovementResponse,
)
from app.services.inventory_reservations import fulfill_reservation, release_and_log, reserve_and_log

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


read_router = APIRouter(
    prefix="/inventory",
    tags=["inventory"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_active_subscription),
    ],
)
admin_router = APIRouter(
    prefix="/inventory",
    tags=["inventory"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_admin_company),
        Depends(require_active_subscription),
    ],
)


class StockMovementRequest(BaseModel):
    warehouse_id: int = Field(..., ge=1)
    product_id: int = Field(..., ge=1)
    qty_delta: int
    reason: str | None = Field(None, max_length=255)
    reference: str | None = Field(None, max_length=64)


def _parse_dt(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be ISO 8601") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _movement_type_for_delta(delta: int) -> str:
    if delta > 0:
        return "in"
    if delta < 0:
        return "out"
    return "adjustment"


def _filter_company(stmt, current_user: User):
    if is_platform_admin(current_user):
        return stmt
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return stmt.where(Warehouse.company_id == company_id)


async def _get_warehouse_or_forbidden(
    db: AsyncSession,
    warehouse_id: int,
    current_user: User,
) -> Warehouse:
    result = await db.execute(select(Warehouse).where(Warehouse.id == warehouse_id))
    warehouse = result.scalar_one_or_none()
    if not warehouse:
        raise NotFoundError("Warehouse not found", "WAREHOUSE_NOT_FOUND")
    if not is_platform_admin(current_user):
        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        if warehouse.company_id != company_id:
            raise AuthorizationError("Forbidden", "FORBIDDEN")
    return warehouse


async def _get_product_or_forbidden(db: AsyncSession, product_id: int, current_user: User) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    if not is_platform_admin(current_user):
        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        if product.company_id != company_id:
            raise AuthorizationError("Forbidden", "FORBIDDEN")
    return product


@read_router.get("/stocks", response_model=PaginatedResponse[ProductStockResponse])
async def list_stocks(
    warehouse_id: int | None = Query(None, ge=1),
    product_id: int | None = Query(None, ge=1),
    q: str | None = Query(None, min_length=0),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = (
        select(ProductStock, Product.sku, Product.name, Warehouse.name)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .join(Product, Product.id == ProductStock.product_id)
    )
    stmt = _filter_company(stmt, current_user)
    stmt = stmt.where(ProductStock.is_archived.is_(False), Warehouse.is_archived.is_(False))

    if warehouse_id is not None:
        stmt = stmt.where(ProductStock.warehouse_id == warehouse_id)
    if product_id is not None:
        stmt = stmt.where(ProductStock.product_id == product_id)
    if q:
        s = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.sku.ilike(s), Product.name.ilike(s), Warehouse.name.ilike(s)))

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(ProductStock.id.desc()).offset(pagination.offset).limit(pagination.limit)
    rows = (await db.execute(items_stmt)).all()

    items = []
    for stock, sku, name, wh_name in rows:
        items.append(
            {
                "product_id": stock.product_id,
                "warehouse_id": stock.warehouse_id,
                "quantity": int(stock.quantity),
                "reserved_quantity": int(stock.reserved_quantity),
                "min_quantity": int(stock.min_quantity),
                "max_quantity": stock.max_quantity,
                "location": stock.location,
                "available_quantity": int(stock.available_quantity),
                "is_low_stock": bool(stock.is_low_stock),
                "product_sku": sku,
                "product_name": name,
                "warehouse_name": wh_name,
            }
        )

    return PaginatedResponse.create(items=items, total=total, page=pagination.page, per_page=pagination.per_page)


@admin_router.post("/movements", response_model=StockMovementResponse, status_code=status.HTTP_201_CREATED)
async def create_stock_movement(
    payload: StockMovementRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    warehouse = await _get_warehouse_or_forbidden(db, payload.warehouse_id, current_user)
    product = await _get_product_or_forbidden(db, payload.product_id, current_user)

    if not is_platform_admin(current_user):
        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        if warehouse.company_id != company_id or product.company_id != company_id:
            raise AuthorizationError("Forbidden", "FORBIDDEN")

    stock_result = await db.execute(
        select(ProductStock)
        .where(ProductStock.product_id == product.id)
        .where(ProductStock.warehouse_id == warehouse.id)
    )
    stock = stock_result.scalar_one_or_none()
    if stock is None:
        stock = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=0, reserved_quantity=0)
        db.add(stock)
        await db.flush()

    prev_qty = int(stock.quantity)
    new_qty = prev_qty + int(payload.qty_delta)
    if new_qty < 0:
        raise SmartSellValidationError(
            "Resulting stock quantity cannot be negative",
            "NEGATIVE_STOCK",
            http_status=422,
        )
    if int(stock.reserved_quantity) > new_qty:
        raise SmartSellValidationError(
            "Resulting stock quantity cannot be below reserved quantity",
            "NEGATIVE_STOCK",
            http_status=422,
        )

    stock.quantity = new_qty

    movement = StockMovement(
        stock_id=stock.id,
        product_id=product.id,
        movement_type=_movement_type_for_delta(int(payload.qty_delta)),
        quantity=int(payload.qty_delta),
        previous_quantity=prev_qty,
        new_quantity=new_qty,
        user_id=getattr(current_user, "id", None),
        reference_type=payload.reference,
        reference_id=None,
        reason=payload.reason,
    )
    db.add(movement)
    await db.commit()
    await db.refresh(movement)

    return {
        "stock_id": movement.stock_id,
        "movement_type": movement.movement_type,
        "quantity": int(movement.quantity),
        "previous_quantity": int(movement.previous_quantity),
        "new_quantity": int(movement.new_quantity),
        "reference_type": movement.reference_type,
        "reference_id": movement.reference_id,
        "reason": movement.reason,
        "notes": movement.notes,
        "user_id": movement.user_id,
        "product_sku": product.sku,
        "product_name": product.name,
        "warehouse_name": warehouse.name,
    }


@read_router.get("/movements", response_model=PaginatedResponse[StockMovementResponse])
async def list_stock_movements(
    warehouse_id: int | None = Query(None, ge=1),
    product_id: int | None = Query(None, ge=1),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = (
        select(StockMovement, Product.sku, Product.name, Warehouse.name)
        .join(ProductStock, ProductStock.id == StockMovement.stock_id)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .join(Product, Product.id == ProductStock.product_id)
    )
    stmt = _filter_company(stmt, current_user)
    stmt = stmt.where(StockMovement.is_archived.is_(False), Warehouse.is_archived.is_(False))

    df = _parse_dt(date_from, "date_from")
    dt = _parse_dt(date_to, "date_to")
    if df:
        stmt = stmt.where(StockMovement.created_at >= df)
    if dt:
        stmt = stmt.where(StockMovement.created_at <= dt)
    if warehouse_id is not None:
        stmt = stmt.where(ProductStock.warehouse_id == warehouse_id)
    if product_id is not None:
        stmt = stmt.where(ProductStock.product_id == product_id)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    items_stmt = items_stmt.offset(pagination.offset).limit(pagination.limit)
    rows = (await db.execute(items_stmt)).all()

    items = []
    for movement, sku, name, wh_name in rows:
        items.append(
            {
                "stock_id": movement.stock_id,
                "movement_type": movement.movement_type,
                "quantity": int(movement.quantity),
                "previous_quantity": int(movement.previous_quantity),
                "new_quantity": int(movement.new_quantity),
                "reference_type": movement.reference_type,
                "reference_id": movement.reference_id,
                "reason": movement.reason,
                "notes": movement.notes,
                "user_id": movement.user_id,
                "product_sku": sku,
                "product_name": name,
                "warehouse_name": wh_name,
            }
        )

    return PaginatedResponse.create(items=items, total=total, page=pagination.page, per_page=pagination.per_page)


@admin_router.post("/reservations/reserve", response_model=InventoryReservationResponse)
async def reserve_inventory(
    payload: InventoryReservationRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    result = await reserve_and_log(
        db,
        tenant_id=company_id,
        product_id=payload.product_id,
        qty=payload.qty,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        warehouse_id=payload.warehouse_id,
    )
    await db.commit()
    return InventoryReservationResponse(**result)


@admin_router.post("/reservations/release", response_model=InventoryReservationResponse)
async def release_inventory(
    payload: InventoryReservationRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    result = await release_and_log(
        db,
        tenant_id=company_id,
        product_id=payload.product_id,
        qty=payload.qty,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        warehouse_id=payload.warehouse_id,
    )
    await db.commit()
    return InventoryReservationResponse(**result)


@admin_router.post("/reservations/fulfill", response_model=InventoryReservationResponse)
async def fulfill_inventory(
    payload: InventoryReservationRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    result = await fulfill_reservation(
        db,
        tenant_id=company_id,
        product_id=payload.product_id,
        qty=payload.qty,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        warehouse_id=payload.warehouse_id,
    )
    await db.commit()
    return InventoryReservationResponse(**result)


router.include_router(read_router)
router.include_router(admin_router)
