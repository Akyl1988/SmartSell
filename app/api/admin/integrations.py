from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json, encrypt_json
from app.core.dependencies import (
    get_db,
    require_platform_admin,
    ensure_idempotency,
    set_idempotency_result,
)
from app.core.provider_registry import ProviderRegistry
from app.models.system_integrations import SystemActiveProvider, SystemIntegration

router = APIRouter(prefix="/api/admin/integrations", tags=["admin-integrations"])

ProviderDomain = Literal["payments", "otp", "messaging"]


class ProviderBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    domain: ProviderDomain = Field(..., description="Integration domain")
    provider: str = Field(..., min_length=1, max_length=128, description="Provider code")
    is_enabled: bool = Field(default=True)
    capabilities: dict[str, Any] | None = Field(default=None)


class ProviderCreate(ProviderBase):
    config: dict[str, Any] = Field(default_factory=dict, description="Provider config payload")


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config: dict[str, Any] | None = Field(default=None)
    is_enabled: bool | None = Field(default=None)
    capabilities: dict[str, Any] | None = Field(default=None)


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    provider: str
    is_enabled: bool
    capabilities: dict[str, Any] | None
    version: int
    updated_at: dt.datetime | None = None
    has_config: bool = True


class ActiveProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    domain: str
    provider: str
    version: int
    updated_at: dt.datetime | None = None


class SetActive(BaseModel):
    domain: ProviderDomain
    provider: str


class CacheInvalidate(BaseModel):
    domain: ProviderDomain | None = None


def _normalize_domain(domain: ProviderDomain | str | None) -> str:
    return (domain or "").strip().lower()


async def _execute(db, stmt):
    if isinstance(db, AsyncSession):
        return await db.execute(stmt)
    return db.execute(stmt)


async def _commit(db) -> None:
    if isinstance(db, AsyncSession):
        await db.commit()
    else:
        db.commit()


async def _refresh(db, model) -> None:
    if isinstance(db, AsyncSession):
        await db.refresh(model)
    else:
        db.refresh(model)


async def _get_integration(db, integration_id: int) -> SystemIntegration | None:
    stmt = select(SystemIntegration).where(SystemIntegration.id == integration_id)
    res = await _execute(db, stmt)
    return res.scalar_one_or_none()


async def _get_integration_by_pair(db, domain: str, provider: str) -> SystemIntegration | None:
    stmt = (
        select(SystemIntegration)
        .where(
            SystemIntegration.domain == domain,
            SystemIntegration.provider == provider,
        )
        .limit(1)
    )
    res = await _execute(db, stmt)
    return res.scalar_one_or_none()


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    domain: ProviderDomain | None = None,
    db=Depends(get_db),
    _: Any = Depends(require_platform_admin),
) -> list[ProviderOut]:
    stmt = select(SystemIntegration)
    if domain:
        stmt = stmt.where(SystemIntegration.domain == _normalize_domain(domain))
    res = await _execute(db, stmt.order_by(SystemIntegration.domain, SystemIntegration.provider))
    items = res.scalars().all()
    out: list[ProviderOut] = []
    for it in items:
        out.append(
            ProviderOut(
                id=it.id,
                domain=it.domain,
                provider=it.provider,
                is_enabled=it.is_enabled,
                capabilities=it.capabilities,
                version=it.version or 1,
                updated_at=it.updated_at,
                has_config=bool(it.config_encrypted),
            )
        )
    return out


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    payload: ProviderCreate,
    db=Depends(get_db),
    _: Any = Depends(require_platform_admin),
) -> ProviderOut:
    encrypted = encrypt_json(payload.config)
    domain = _normalize_domain(payload.domain)
    item = SystemIntegration(
        domain=domain,
        provider=payload.provider,
        config_encrypted=encrypted,
        is_enabled=payload.is_enabled,
        capabilities=payload.capabilities,
        version=1,
    )
    db.add(item)
    await _commit(db)
    await _refresh(db, item)
    await ProviderRegistry.notify_change(domain, item.version or 1)
    return ProviderOut(
        id=item.id,
        domain=item.domain,
        provider=item.provider,
        is_enabled=item.is_enabled,
        capabilities=item.capabilities,
        version=item.version or 1,
        updated_at=item.updated_at,
        has_config=bool(item.config_encrypted),
    )


@router.put("/providers/{integration_id}", response_model=ProviderOut)
async def update_provider(
    integration_id: int,
    payload: ProviderUpdate,
    db=Depends(get_db),
    _: Any = Depends(require_platform_admin),
) -> ProviderOut:
    item = await _get_integration(db, integration_id)
    if not item:
        raise HTTPException(status_code=404, detail="integration_not_found")

    changed = False
    if payload.config is not None:
        item.config_encrypted = encrypt_json(payload.config)
        changed = True
    if payload.is_enabled is not None:
        item.is_enabled = payload.is_enabled
        changed = True
    if payload.capabilities is not None:
        item.capabilities = payload.capabilities
        changed = True

    if changed:
        item.version = (item.version or 1) + 1

    db.add(item)
    await _commit(db)
    await _refresh(db, item)
    await ProviderRegistry.notify_change(item.domain, item.version)

    return ProviderOut(
        id=item.id,
        domain=item.domain,
        provider=item.provider,
        is_enabled=item.is_enabled,
        capabilities=item.capabilities,
        version=item.version or 1,
        updated_at=item.updated_at,
        has_config=bool(item.config_encrypted),
    )


@router.delete("/providers/{integration_id}", status_code=200)
async def delete_provider(
    integration_id: int,
    db=Depends(get_db),
    _: Any = Depends(require_platform_admin),
) -> dict[str, Any]:
    item = await _get_integration(db, integration_id)
    if not item:
        return {"deleted": False}
    domain = item.domain
    provider = item.provider
    db.delete(item)
    active_stmt = (
        select(SystemActiveProvider)
        .where(
            SystemActiveProvider.domain == domain,
            SystemActiveProvider.provider == provider,
        )
        .limit(1)
    )
    res = await _execute(db, active_stmt)
    active = res.scalar_one_or_none()
    active_cleared = False
    if active:
        db.delete(active)
        active_cleared = True

    await _commit(db)
    await ProviderRegistry.notify_change(domain, None)
    return {"deleted": True, "active_cleared": active_cleared}


@router.get("/active/{domain}", response_model=ActiveProviderOut)
async def get_active_provider(
    domain: ProviderDomain = Path(..., description="Domain"),
    db=Depends(get_db),
    _: Any = Depends(require_platform_admin),
) -> ActiveProviderOut:
    stmt = select(SystemActiveProvider).where(
        SystemActiveProvider.domain == _normalize_domain(domain)
    )
    res = await _execute(db, stmt)
    active = res.scalar_one_or_none()
    if not active:
        raise HTTPException(status_code=404, detail="active_provider_not_set")
    return ActiveProviderOut(
        domain=active.domain,
        provider=active.provider,
        version=active.version or 1,
        updated_at=active.updated_at,
    )


@router.post(
    "/active",
    response_model=ActiveProviderOut,
    dependencies=[Depends(ensure_idempotency)],
)
async def set_active_provider(
    payload: SetActive,
    request: Request,
    db=Depends(get_db),
    _: Any = Depends(require_platform_admin),
) -> ActiveProviderOut:
    domain = _normalize_domain(payload.domain)
    integ = await _get_integration_by_pair(db, domain, payload.provider)
    if not integ or not integ.is_enabled:
        raise HTTPException(status_code=404, detail="integration_not_found_or_disabled")

    stmt = select(SystemActiveProvider).where(SystemActiveProvider.domain == domain)
    res = await _execute(db, stmt)
    active = res.scalar_one_or_none()
    new_version = 1
    if active:
        new_version = (active.version or 0) + 1
        active.provider = payload.provider
        active.version = new_version
        db.add(active)
    else:
        active = SystemActiveProvider(
            domain=domain,
            provider=payload.provider,
            version=new_version,
        )
        db.add(active)

    await _commit(db)
    await _refresh(db, active)
    await ProviderRegistry.notify_change(domain, new_version)

    idem_key = getattr(getattr(request, "state", None), "idempotency_key", None)
    if idem_key:
        await set_idempotency_result(idem_key, status_code=200, ttl_seconds=None)  # type: ignore[arg-type]

    return ActiveProviderOut(
        domain=active.domain,
        provider=active.provider,
        version=active.version or 1,
        updated_at=active.updated_at,
    )


@router.post("/cache/invalidate", response_model=dict[str, str])
async def invalidate_cache(
    payload: CacheInvalidate,
    _: Any = Depends(require_platform_admin),
) -> dict[str, str]:
    domain = _normalize_domain(payload.domain)
    ProviderRegistry.invalidate(domain or None)
    await ProviderRegistry.publish_change(domain or "", None)
    return {"status": "ok", "domain": domain or "*"}
