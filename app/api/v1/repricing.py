from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_async_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_verified_user,
    get_pagination,
    require_company_access,
    require_store_roles,
)
from app.core.exceptions import NotFoundError
from app.core.security import resolve_tenant_company_id
from app.models.repricing import RepricingRule, RepricingRun
from app.models.user import User
from app.schemas.base import SuccessResponse
from app.schemas.repricing import (
    RepricingRuleCreate,
    RepricingRuleListResponse,
    RepricingRuleResponse,
    RepricingRuleUpdate,
    RepricingRunItemResponse,
    RepricingRunListResponse,
    RepricingRunResponse,
    RepricingRunTriggerResponse,
)
from app.services.repricing import run_reprcing_for_company, validate_rule

router = APIRouter()


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


store_router = APIRouter(
    prefix="/repricing",
    tags=["repricing"],
    dependencies=[
        Depends(api_rate_limit),
        Depends(require_company_access),
        Depends(require_store_roles("admin")),
        Depends(_require_company_context),
    ],
)


def _apply_rule_updates(rule: RepricingRule, payload: RepricingRuleUpdate) -> None:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(rule, key, value)


async def _get_rule_or_404(db: AsyncSession, rule_id: int, company_id: int) -> RepricingRule:
    result = await db.execute(
        select(RepricingRule).where(RepricingRule.id == rule_id, RepricingRule.company_id == company_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("Rule not found", "RULE_NOT_FOUND")
    return rule


@store_router.get("/rules", response_model=RepricingRuleListResponse)
async def list_rules(
    include_inactive: bool = Query(False),
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    stmt = select(RepricingRule).where(RepricingRule.company_id == company_id)
    if not include_inactive:
        stmt = stmt.where(RepricingRule.is_active.is_(True))

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(RepricingRule.id.desc()).offset(pagination.offset).limit(pagination.limit)
    items = (await db.execute(items_stmt)).scalars().all()

    return RepricingRuleListResponse.create(
        items=items, total=total, page=pagination.page, per_page=pagination.per_page
    )


@store_router.post("/rules", response_model=RepricingRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RepricingRuleCreate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    rule = RepricingRule(company_id=company_id, **payload.model_dump())
    validate_rule(rule)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@store_router.get("/rules/{rule_id}", response_model=RepricingRuleResponse)
async def get_rule(
    rule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return await _get_rule_or_404(db, rule_id, company_id)


@store_router.patch("/rules/{rule_id}", response_model=RepricingRuleResponse)
async def update_rule(
    payload: RepricingRuleUpdate,
    rule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    rule = await _get_rule_or_404(db, rule_id, company_id)
    _apply_rule_updates(rule, payload)
    validate_rule(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@store_router.delete("/rules/{rule_id}", response_model=SuccessResponse)
async def delete_rule(
    rule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    rule = await _get_rule_or_404(db, rule_id, company_id)
    if not rule.is_active:
        return SuccessResponse(message="Rule already inactive")
    rule.is_active = False
    rule.enabled = False
    await db.commit()
    return SuccessResponse(message="Rule deleted successfully")


@store_router.post("/run", response_model=RepricingRunTriggerResponse)
async def run_repricing(
    request: Request,
    dry_run: bool = Query(False),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID")
    run = await run_reprcing_for_company(
        db,
        company_id,
        triggered_by_user_id=getattr(current_user, "id", None),
        dry_run=dry_run,
        request_id=request_id,
    )
    await db.commit()
    await db.refresh(run)
    return RepricingRunTriggerResponse(run_id=run.id)


@store_router.get("/runs", response_model=RepricingRunListResponse)
async def list_runs(
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    stmt = select(RepricingRun).where(RepricingRun.company_id == company_id)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())
    items_stmt = stmt.order_by(RepricingRun.created_at.desc()).offset(pagination.offset).limit(pagination.limit)
    items = (await db.execute(items_stmt)).scalars().all()

    payload_items = [RepricingRunResponse.model_validate(item) for item in items]
    return RepricingRunListResponse.create(
        items=payload_items, total=total, page=pagination.page, per_page=pagination.per_page
    )


@store_router.get("/runs/{run_id}", response_model=RepricingRunResponse)
async def get_run(
    run_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    result = await db.execute(
        select(RepricingRun)
        .where(RepricingRun.id == run_id, RepricingRun.company_id == company_id)
        .options(selectinload(RepricingRun.items))
    )
    run = result.scalar_one_or_none()
    if not run:
        raise NotFoundError("Run not found", "RUN_NOT_FOUND")

    items = sorted(run.items or [], key=lambda item: item.id or 0)
    payload_items = [RepricingRunItemResponse.model_validate(item) for item in items]
    response = RepricingRunResponse.model_validate(run)
    return response.model_copy(update={"items": payload_items})


router.include_router(store_router)
