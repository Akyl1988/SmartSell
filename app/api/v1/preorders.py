from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status
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
from app.core.security import resolve_tenant_company_id
from app.models.user import User
from app.schemas.preorders import (
    PreorderCreateIn,
    PreorderListFilters,
    PreorderListResponse,
    PreorderOut,
    PreorderUpdateIn,
)
from app.services.preorders import (
    cancel_preorder,
    confirm_preorder,
    create_preorder,
    fulfill_preorder,
    get_preorder,
    list_preorders,
    update_preorder,
)

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


store_router = APIRouter(
    prefix="/preorders",
    tags=["preorders"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(require_store_roles("admin")),
        Depends(_require_company_context),
    ],
)


@store_router.post("", response_model=PreorderOut, status_code=status.HTTP_201_CREATED)
async def create_preorder_endpoint(
    payload: PreorderCreateIn,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await create_preorder(
        db,
        company_id=company_id,
        created_by_user_id=getattr(current_user, "id", None),
        payload=payload,
    )
    return PreorderOut.model_validate(preorder)


@store_router.get("", response_model=PreorderListResponse)
async def list_preorders_endpoint(
    filters: PreorderListFilters = Depends(),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    items, total = await list_preorders(
        db,
        company_id=company_id,
        filters=filters,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    payload_items = [PreorderOut.model_validate(item) for item in items]
    return PreorderListResponse.create(
        items=payload_items, total=total, page=pagination.page, per_page=pagination.per_page
    )


@store_router.get("/{preorder_id}", response_model=PreorderOut)
async def get_preorder_endpoint(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await get_preorder(db, company_id=company_id, preorder_id=preorder_id)
    return PreorderOut.model_validate(preorder)


@store_router.patch("/{preorder_id}", response_model=PreorderOut)
async def update_preorder_endpoint(
    payload: PreorderUpdateIn,
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await update_preorder(db, company_id=company_id, preorder_id=preorder_id, payload=payload)
    return PreorderOut.model_validate(preorder)


@store_router.post("/{preorder_id}/confirm", response_model=PreorderOut)
async def confirm_preorder_endpoint(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await confirm_preorder(db, company_id=company_id, preorder_id=preorder_id)
    return PreorderOut.model_validate(preorder)


@store_router.post("/{preorder_id}/cancel", response_model=PreorderOut)
async def cancel_preorder_endpoint(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await cancel_preorder(db, company_id=company_id, preorder_id=preorder_id)
    return PreorderOut.model_validate(preorder)


@store_router.post("/{preorder_id}/fulfill", response_model=PreorderOut)
async def fulfill_preorder_endpoint(
    preorder_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    preorder = await fulfill_preorder(db, company_id=company_id, preorder_id=preorder_id)
    return PreorderOut.model_validate(preorder)


router.include_router(store_router)
