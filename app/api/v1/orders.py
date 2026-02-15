from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.base import PaginatedResponse
from app.schemas.order import OrderResponse

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


read_router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_active_subscription),
    ],
)
admin_router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_admin_company),
        Depends(require_active_subscription),
    ],
)


class OrderInternalUpdate(BaseModel):
    internal_notes: str | None = Field(None, max_length=4000)
    internal_status: OrderStatus | None = None


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


def _filter_company(stmt, user: User):
    if is_platform_admin(user):
        return stmt
    cid = resolve_tenant_company_id(user, not_found_detail="Company not set")
    return stmt.where(Order.company_id == cid)


async def _get_order_or_forbidden(db: AsyncSession, order_id: int, user: User) -> Order:
    stmt = select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError("Order not found", "ORDER_NOT_FOUND")
    if not is_platform_admin(user):
        cid = resolve_tenant_company_id(user, not_found_detail="Company not set")
        if order.company_id != cid:
            raise AuthorizationError("Forbidden", "FORBIDDEN")
    return order


@read_router.get("", response_model=PaginatedResponse[OrderResponse])
async def list_orders(
    status: OrderStatus | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    q: str | None = Query(None, min_length=0),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = select(Order).options(selectinload(Order.items))
    stmt = _filter_company(stmt, current_user)

    if status is not None:
        stmt = stmt.where(Order.status == status)

    df = _parse_dt(date_from, "date_from")
    dt = _parse_dt(date_to, "date_to")
    if df:
        stmt = stmt.where(Order.created_at >= df)
    if dt:
        stmt = stmt.where(Order.created_at <= dt)

    if q:
        s = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Order.order_number.ilike(s),
                Order.external_id.ilike(s),
                Order.customer_phone.ilike(s),
                Order.customer_email.ilike(s),
                Order.customer_name.ilike(s),
            )
        )

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = (
        stmt.order_by(Order.created_at.desc(), Order.id.desc()).offset(pagination.offset).limit(pagination.limit)
    )
    orders = (await db.execute(items_stmt)).scalars().all()

    return PaginatedResponse.create(
        items=orders,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
    )


@read_router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await _get_order_or_forbidden(db, order_id, current_user)


@admin_router.patch("/{order_id}", response_model=OrderResponse)
async def update_order_internal(
    payload: OrderInternalUpdate,
    order_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    order = await _get_order_or_forbidden(db, order_id, current_user)
    changed = False

    if payload.internal_notes is not None:
        order.internal_notes = payload.internal_notes
        changed = True

    if payload.internal_status is not None:
        try:
            order.change_status(
                payload.internal_status,
                user_id=getattr(current_user, "id", None),
                note="internal_status_update",
                session=db,
            )
        except ValueError as exc:
            raise SmartSellValidationError(str(exc), "INVALID_STATUS_TRANSITION", http_status=422) from exc
        changed = True

    if changed:
        await db.commit()
        await db.refresh(order)

    return order


router.include_router(read_router)
router.include_router(admin_router)
