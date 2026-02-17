from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import require_platform_admin
from app.core.exceptions import ConflictError, NotFoundError
from app.models.subscription_catalog import Feature, Plan, PlanFeature
from app.schemas.subscription_catalog import (
    FeatureCreate,
    FeatureOut,
    FeatureUpdate,
    PlanCreate,
    PlanFeatureOut,
    PlanFeatureUpsert,
    PlanOut,
    PlanUpdate,
)

router = APIRouter(tags=["admin-plans"], dependencies=[Depends(require_platform_admin)])


def _normalize_code(value: str | None) -> str:
    return (value or "").strip().lower()


def _plan_feature_out(row: tuple[PlanFeature, Plan, Feature]) -> PlanFeatureOut:
    plan_feature, plan, feature = row
    return PlanFeatureOut(
        id=plan_feature.id,
        plan_code=plan.code,
        feature_code=feature.code,
        enabled=plan_feature.enabled,
        limits=plan_feature.limits_json,
        created_at=plan_feature.created_at,
        updated_at=plan_feature.updated_at,
    )


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> list[PlanOut]:
    _ = admin
    stmt = select(Plan)
    if not include_inactive:
        stmt = stmt.where(Plan.is_active.is_(True))
    items = (await db.execute(stmt.order_by(Plan.id.asc()))).scalars().all()
    return [PlanOut.model_validate(it) for it in items]


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(
    payload: PlanCreate,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> PlanOut:
    _ = admin
    plan = Plan(
        code=_normalize_code(payload.code),
        name=payload.name,
        price=payload.price,
        currency=payload.currency,
        is_active=payload.is_active,
        trial_days_default=payload.trial_days_default,
    )
    db.add(plan)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("plan_code_exists", code="plan_code_exists", http_status=409) from exc
    await db.refresh(plan)
    return PlanOut.model_validate(plan)


@router.get("/plans/{code}", response_model=PlanOut)
async def get_plan(
    code: str,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> PlanOut:
    _ = admin
    plan = (await db.execute(select(Plan).where(Plan.code == _normalize_code(code)))).scalar_one_or_none()
    if not plan:
        raise NotFoundError("plan_not_found", code="plan_not_found", http_status=404)
    return PlanOut.model_validate(plan)


@router.patch("/plans/{code}", response_model=PlanOut)
async def update_plan(
    code: str,
    payload: PlanUpdate,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> PlanOut:
    _ = admin
    plan = (await db.execute(select(Plan).where(Plan.code == _normalize_code(code)))).scalar_one_or_none()
    if not plan:
        raise NotFoundError("plan_not_found", code="plan_not_found", http_status=404)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(plan, key, value)
    await db.commit()
    await db.refresh(plan)
    return PlanOut.model_validate(plan)


@router.delete("/plans/{code}", response_model=PlanOut)
async def deactivate_plan(
    code: str,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> PlanOut:
    _ = admin
    plan = (await db.execute(select(Plan).where(Plan.code == _normalize_code(code)))).scalar_one_or_none()
    if not plan:
        raise NotFoundError("plan_not_found", code="plan_not_found", http_status=404)
    plan.is_active = False
    await db.commit()
    await db.refresh(plan)
    return PlanOut.model_validate(plan)


@router.get("/features", response_model=list[FeatureOut])
async def list_features(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> list[FeatureOut]:
    _ = admin
    stmt = select(Feature)
    if not include_inactive:
        stmt = stmt.where(Feature.is_active.is_(True))
    items = (await db.execute(stmt.order_by(Feature.id.asc()))).scalars().all()
    return [FeatureOut.model_validate(it) for it in items]


@router.post("/features", response_model=FeatureOut, status_code=201)
async def create_feature(
    payload: FeatureCreate,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> FeatureOut:
    _ = admin
    feature = Feature(
        code=_normalize_code(payload.code),
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    db.add(feature)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("feature_code_exists", code="feature_code_exists", http_status=409) from exc
    await db.refresh(feature)
    return FeatureOut.model_validate(feature)


@router.get("/features/{code}", response_model=FeatureOut)
async def get_feature(
    code: str,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> FeatureOut:
    _ = admin
    feature = (await db.execute(select(Feature).where(Feature.code == _normalize_code(code)))).scalar_one_or_none()
    if not feature:
        raise NotFoundError("feature_not_found", code="feature_not_found", http_status=404)
    return FeatureOut.model_validate(feature)


@router.patch("/features/{code}", response_model=FeatureOut)
async def update_feature(
    code: str,
    payload: FeatureUpdate,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> FeatureOut:
    _ = admin
    feature = (await db.execute(select(Feature).where(Feature.code == _normalize_code(code)))).scalar_one_or_none()
    if not feature:
        raise NotFoundError("feature_not_found", code="feature_not_found", http_status=404)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(feature, key, value)
    await db.commit()
    await db.refresh(feature)
    return FeatureOut.model_validate(feature)


@router.delete("/features/{code}", response_model=FeatureOut)
async def deactivate_feature(
    code: str,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> FeatureOut:
    _ = admin
    feature = (await db.execute(select(Feature).where(Feature.code == _normalize_code(code)))).scalar_one_or_none()
    if not feature:
        raise NotFoundError("feature_not_found", code="feature_not_found", http_status=404)
    feature.is_active = False
    await db.commit()
    await db.refresh(feature)
    return FeatureOut.model_validate(feature)


@router.get("/plan-features", response_model=list[PlanFeatureOut])
async def list_plan_features(
    plan_code: str | None = Query(default=None),
    feature_code: str | None = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> list[PlanFeatureOut]:
    _ = admin
    stmt = (
        select(PlanFeature, Plan, Feature)
        .join(Plan, Plan.id == PlanFeature.plan_id)
        .join(Feature, Feature.id == PlanFeature.feature_id)
    )
    if plan_code:
        stmt = stmt.where(Plan.code == _normalize_code(plan_code))
    if feature_code:
        stmt = stmt.where(Feature.code == _normalize_code(feature_code))
    rows = (await db.execute(stmt.order_by(Plan.id.asc(), Feature.id.asc()))).all()
    return [_plan_feature_out(row) for row in rows]


@router.put("/plan-features/{plan_code}/{feature_code}", response_model=PlanFeatureOut)
async def upsert_plan_feature(
    plan_code: str,
    feature_code: str,
    payload: PlanFeatureUpsert,
    db: AsyncSession = Depends(get_async_db),
    admin: Any = Depends(require_platform_admin),
) -> PlanFeatureOut:
    _ = admin
    plan = (await db.execute(select(Plan).where(Plan.code == _normalize_code(plan_code)))).scalar_one_or_none()
    if not plan:
        raise NotFoundError("plan_not_found", code="plan_not_found", http_status=404)
    feature = (
        await db.execute(select(Feature).where(Feature.code == _normalize_code(feature_code)))
    ).scalar_one_or_none()
    if not feature:
        raise NotFoundError("feature_not_found", code="feature_not_found", http_status=404)

    stmt = select(PlanFeature).where(PlanFeature.plan_id == plan.id, PlanFeature.feature_id == feature.id)
    plan_feature = (await db.execute(stmt)).scalar_one_or_none()
    if plan_feature:
        plan_feature.enabled = payload.enabled
        plan_feature.limits_json = payload.limits
    else:
        plan_feature = PlanFeature(
            plan_id=plan.id,
            feature_id=feature.id,
            enabled=payload.enabled,
            limits_json=payload.limits,
        )
        db.add(plan_feature)

    await db.commit()
    await db.refresh(plan_feature)
    return PlanFeatureOut(
        id=plan_feature.id,
        plan_code=plan.code,
        feature_code=feature.code,
        enabled=plan_feature.enabled,
        limits=plan_feature.limits_json,
        created_at=plan_feature.created_at,
        updated_at=plan_feature.updated_at,
    )
