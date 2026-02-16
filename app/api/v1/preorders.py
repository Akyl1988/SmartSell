from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_verified_user,
    get_pagination,
    require_company_access,
    require_store_roles,
)
from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.core.security import resolve_tenant_company_id
from app.core.subscriptions.features import require_feature
from app.models.order import Order, OrderItem, OrderSource, OrderStatus
from app.models.preorder import Preorder, PreorderStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.base import PaginatedResponse
from app.schemas.preorder import PreorderCreate, PreorderListFilters, PreorderResponse
from app.services.subscription_features import enforce_feature_limit

FEATURE_PREORDERS = "preorders"

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


read_router = APIRouter(
    prefix="/preorders",
    tags=["preorders"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(require_store_roles("admin", "manager", "employee")),
        Depends(_require_company_context),
        Depends(require_feature(FEATURE_PREORDERS)),
    ],
)
admin_router = APIRouter(
    prefix="/preorders",
    tags=["preorders"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(require_store_roles("admin", "manager")),
        Depends(_require_company_context),
        Depends(require_feature(FEATURE_PREORDERS)),
    ],
)


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


async def _get_product_or_404(db: AsyncSession, company_id: int, product_id: int) -> Product:
    result = await db.execute(
        select(Product).where(
            Product.id == product_id,
            Product.company_id == company_id,
            Product.deleted_at.is_(None),
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    return product


async def _get_preorder_or_404(db: AsyncSession, company_id: int, preorder_id: int) -> Preorder:
    result = await db.execute(select(Preorder).where(Preorder.id == preorder_id, Preorder.company_id == company_id))
    preorder = result.scalar_one_or_none()
    if not preorder:
        raise NotFoundError("Preorder not found", "PREORDER_NOT_FOUND")
    return preorder


async def _get_preorder_locked(db: AsyncSession, company_id: int, preorder_id: int) -> Preorder:
    result = await db.execute(
        select(Preorder).where(Preorder.id == preorder_id, Preorder.company_id == company_id).with_for_update()
    )
    preorder = result.scalar_one_or_none()
    if not preorder:
        raise NotFoundError("Preorder not found", "PREORDER_NOT_FOUND")
    return preorder


@admin_router.post("", response_model=PreorderResponse, status_code=status.HTTP_201_CREATED)
async def create_preorder(
    payload: PreorderCreate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    now = datetime.utcnow()
    nested = db.in_transaction()
    tx = db.begin_nested() if nested else db.begin()
    async with tx:
        product = await _get_product_or_404(db, company_id, payload.product_id)

        if not product.is_preorder_enabled:
            raise SmartSellValidationError("Product is not available for preorder", "PREORDER_DISABLED")

        if product.preorder_until is not None:
            now_epoch = int(now.timestamp())
            if int(product.preorder_until) < now_epoch:
                raise SmartSellValidationError("Preorder window has expired", "PREORDER_EXPIRED")

        await enforce_feature_limit(
            db,
            company_id=company_id,
            feature_code=FEATURE_PREORDERS,
            increment_by=1,
            limit_key="max_preorders_per_period",
            now=now,
        )

        preorder = Preorder(
            company_id=company_id,
            product_id=product.id,
            qty=int(payload.qty),
            customer_name=payload.customer_name,
            customer_phone=payload.customer_phone,
            comment=payload.comment,
            status=PreorderStatus.CREATED,
        )
        preorder.snapshot_from_product(
            preorder_until=product.preorder_until,
            deposit=product.preorder_deposit,
        )
        db.add(preorder)
    if nested:
        await db.commit()
    await db.refresh(preorder)
    return preorder


@read_router.get("", response_model=PaginatedResponse[PreorderResponse])
async def list_preorders(
    filters: PreorderListFilters = Depends(),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    stmt = select(Preorder).where(Preorder.company_id == company_id)

    if filters.status is not None:
        stmt = stmt.where(Preorder.status == filters.status)
    if filters.product_id is not None:
        stmt = stmt.where(Preorder.product_id == filters.product_id)

    df = _parse_dt(filters.date_from, "date_from")
    dt = _parse_dt(filters.date_to, "date_to")
    if df:
        stmt = stmt.where(Preorder.created_at >= df)
    if dt:
        stmt = stmt.where(Preorder.created_at <= dt)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(Preorder.created_at.desc(), Preorder.id.desc())
    items_stmt = items_stmt.offset(pagination.offset).limit(pagination.limit)
    items = (await db.execute(items_stmt)).scalars().all()

    return PaginatedResponse.create(items=items, total=total, page=pagination.page, per_page=pagination.per_page)


@read_router.get("/{preorder_id}", response_model=PreorderResponse)
async def get_preorder(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return await _get_preorder_or_404(db, company_id, preorder_id)


@admin_router.post("/{preorder_id}/confirm", response_model=PreorderResponse)
async def confirm_preorder(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    _ = current_user
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await _get_preorder_or_404(db, company_id, preorder_id)
    try:
        preorder.confirm()
    except ValueError as exc:
        raise SmartSellValidationError(str(exc), "INVALID_PREORDER_STATUS") from exc
    await db.commit()
    await db.refresh(preorder)
    return preorder


@admin_router.post("/{preorder_id}/cancel", response_model=PreorderResponse)
async def cancel_preorder(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    _ = current_user
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await _get_preorder_or_404(db, company_id, preorder_id)
    try:
        preorder.cancel()
    except ValueError as exc:
        raise SmartSellValidationError(str(exc), "INVALID_PREORDER_STATUS") from exc
    await db.commit()
    await db.refresh(preorder)
    return preorder


@admin_router.post("/{preorder_id}/convert-to-order", response_model=PreorderResponse)
async def convert_preorder_to_order(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    nested = db.in_transaction()
    tx = db.begin_nested() if nested else db.begin()
    async with tx:
        preorder = await _get_preorder_locked(db, company_id, preorder_id)
        if preorder.status == PreorderStatus.CONVERTED or preorder.converted_order_id:
            raise SmartSellValidationError(
                "Preorder already converted",
                "PREORDER_ALREADY_CONVERTED",
                http_status=409,
            )
        if preorder.status != PreorderStatus.CONFIRMED:
            raise SmartSellValidationError(
                "Preorder must be confirmed before conversion",
                "INVALID_PREORDER_STATUS",
                http_status=422,
            )

        product = await _get_product_or_404(db, company_id, preorder.product_id)
        if product.price is None:
            raise SmartSellValidationError("Product price is missing", "PRODUCT_PRICE_MISSING")
        if not product.sku or not product.name:
            raise SmartSellValidationError("Product SKU and name are required", "PRODUCT_DATA_MISSING")

        order = Order(
            company_id=company_id,
            order_number=f"PRE-{uuid4().hex[:10]}",
            source=OrderSource.MANUAL,
            status=OrderStatus.CONFIRMED,
            customer_name=preorder.customer_name,
            customer_phone=preorder.customer_phone,
            notes=preorder.comment,
        )
        unit_price = Decimal(str(product.price))
        quantity = int(preorder.qty)
        item_total = (unit_price * Decimal(quantity)).quantize(Decimal("0.01"))
        OrderItem(
            order=order,
            product_id=product.id,
            sku=product.sku,
            name=product.name,
            unit_price=unit_price,
            quantity=quantity,
            total_price=item_total,
            cost_price=Decimal("0"),
        )
        order.calculate_totals()
        db.add(order)
        await db.flush()

        preorder.mark_converted(order.id)
    if nested:
        await db.commit()
    await db.refresh(preorder)
    return preorder


router.include_router(read_router)
router.include_router(admin_router)
