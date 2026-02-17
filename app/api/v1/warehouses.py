from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.core.rbac import is_platform_admin
from app.core.security import resolve_tenant_company_id
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas.base import PaginatedResponse
from app.schemas.warehouse import WarehouseCreate, WarehouseResponse, WarehouseUpdate

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


read_router = APIRouter(
    prefix="/warehouses",
    tags=["warehouses"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_active_subscription),
    ],
)
admin_router = APIRouter(
    prefix="/warehouses",
    tags=["warehouses"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_admin_company),
        Depends(require_active_subscription),
    ],
)


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
    stmt = select(Warehouse).where(Warehouse.id == warehouse_id)
    result = await db.execute(stmt)
    warehouse = result.scalar_one_or_none()
    if not warehouse:
        raise NotFoundError("Warehouse not found", "WAREHOUSE_NOT_FOUND")
    if not is_platform_admin(current_user):
        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        if warehouse.company_id != company_id:
            raise AuthorizationError("Forbidden", "FORBIDDEN")
    return warehouse


@read_router.get("", response_model=PaginatedResponse[WarehouseResponse])
async def list_warehouses(
    include_archived: bool = Query(False),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = select(Warehouse)
    stmt = _filter_company(stmt, current_user)
    if not include_archived:
        stmt = stmt.where(Warehouse.is_archived.is_(False))

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(Warehouse.id.desc()).offset(pagination.offset).limit(pagination.limit)
    items = (await db.execute(items_stmt)).scalars().all()

    return PaginatedResponse.create(items=items, total=total, page=pagination.page, per_page=pagination.per_page)


@admin_router.post("", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED)
async def create_warehouse(
    payload: WarehouseCreate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    warehouse = Warehouse(company_id=company_id, **payload.model_dump())
    db.add(warehouse)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Warehouse already exists", "DUPLICATE_WAREHOUSE", http_status=409) from exc
    await db.refresh(warehouse)
    return warehouse


@read_router.get("/{warehouse_id}", response_model=WarehouseResponse)
async def get_warehouse(
    warehouse_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await _get_warehouse_or_forbidden(db, warehouse_id, current_user)


@admin_router.patch("/{warehouse_id}", response_model=WarehouseResponse)
async def update_warehouse(
    payload: WarehouseUpdate,
    warehouse_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    warehouse = await _get_warehouse_or_forbidden(db, warehouse_id, current_user)
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(warehouse, key, value)
    await db.commit()
    await db.refresh(warehouse)
    return warehouse


router.include_router(read_router)
router.include_router(admin_router)
