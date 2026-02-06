from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.dependencies import (
    ensure_idempotency_replay,
    get_db,
    require_platform_admin,
    set_idempotency_result,
)
from app.core.provider_registry import ProviderRegistry
from app.services.integration_providers import IntegrationProviderService
from app.services.provider_configs import ProviderConfigService

router = APIRouter(prefix="/api/admin/integrations", tags=["admin-integrations"])

ProviderDomain = Literal["payments", "otp", "messaging"]


class ProviderBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    domain: ProviderDomain = Field(..., description="Integration domain")
    provider: str = Field(..., min_length=1, max_length=128, description="Provider code")


class ProviderCreate(ProviderBase):
    config: dict[str, Any] | None = Field(default_factory=dict, description="Provider config payload")
    capabilities: dict[str, Any] | None = Field(default=None)
    is_enabled: bool = Field(default=True)
    is_active: bool = Field(default=False)


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config: dict[str, Any] | None = Field(default=None)
    capabilities: dict[str, Any] | None = Field(default=None)
    is_enabled: bool | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    provider: str
    is_enabled: bool
    is_active: bool
    capabilities: dict[str, Any] | None
    version: int
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    has_config: bool = True


class ActiveProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    domain: str
    provider: str
    version: int
    updated_at: dt.datetime | None = None


class ProviderEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    provider_from: str | None
    provider_to: str
    actor_user_id: int | None
    created_at: dt.datetime
    updated_at: dt.datetime | None
    meta_json: dict[str, Any] | None = None


class SetActive(BaseModel):
    domain: ProviderDomain
    provider: str


class CacheInvalidate(BaseModel):
    domain: ProviderDomain | None = None


class ProviderConfigIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config: dict[str, Any] = Field(..., description="Provider config payload")
    key_id: str | None = Field(default=None, description="Key identifier used for encryption")
    meta: dict[str, Any] | None = Field(default=None)


class ProviderConfigOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    domain: str
    provider: str
    config: dict[str, Any]
    key_id: str | None = None
    updated_at: dt.datetime | None = None


class ProviderHealthcheckOut(BaseModel):
    status: str
    domain: str
    provider: str
    error: str | None = None


class PaymentConfigUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(..., min_length=1, max_length=128)
    config: dict[str, Any] = Field(..., description="Provider config payload")
    key_id: str | None = Field(default=None, description="Key identifier used for encryption")
    meta: dict[str, Any] | None = Field(default=None)


class MessagingConfigUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(..., min_length=1, max_length=128)
    config: dict[str, Any] = Field(..., description="Provider config payload")
    key_id: str | None = Field(default=None, description="Key identifier used for encryption")
    meta: dict[str, Any] | None = Field(default=None)


def _normalize_domain(domain: ProviderDomain | str | None) -> str:
    return (domain or "").strip().lower()


def _require_config_or_404(config: dict[str, Any]) -> dict[str, Any]:
    if not config:
        raise HTTPException(status_code=404, detail="config_not_found")
    return config


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    domain: ProviderDomain | None = None,
    provider: str | None = None,
    is_enabled: bool | None = None,
    is_active: bool | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> list[ProviderOut]:
    _ = admin  # access control only
    items = await IntegrationProviderService.list_providers(
        db,
        domain=_normalize_domain(domain),
        provider=provider,
        is_enabled=is_enabled,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    out: list[ProviderOut] = []
    for it in items:
        out.append(
            ProviderOut(
                id=it.id,
                domain=it.domain,
                provider=it.provider,
                is_enabled=it.is_enabled,
                is_active=it.is_active,
                capabilities=it.capabilities,
                version=it.version or 1,
                created_at=it.created_at,
                updated_at=it.updated_at,
                has_config=it.config_json is not None,
            )
        )
    return out


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    payload: ProviderCreate,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderOut:
    item = await IntegrationProviderService.create_provider(
        db,
        domain=_normalize_domain(payload.domain),
        provider=payload.provider,
        config=payload.config or {},
        capabilities=payload.capabilities,
        is_enabled=payload.is_enabled,
        is_active=payload.is_active,
        actor_user_id=getattr(admin, "id", None),
        actor_email=getattr(admin, "email", None),
    )
    await ProviderRegistry.notify_change(item.domain, item.version or 1)
    return ProviderOut(
        id=item.id,
        domain=item.domain,
        provider=item.provider,
        is_enabled=item.is_enabled,
        is_active=item.is_active,
        capabilities=item.capabilities,
        version=item.version or 1,
        updated_at=item.updated_at,
        has_config=item.config_json is not None,
    )


@router.put("/providers/{integration_id}", response_model=ProviderOut)
async def update_provider(
    integration_id: int,
    payload: ProviderUpdate,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderOut:
    item = await IntegrationProviderService.update_provider(
        db,
        integration_id,
        config=payload.config,
        capabilities=payload.capabilities,
        is_enabled=payload.is_enabled,
        is_active=payload.is_active,
        actor_user_id=getattr(admin, "id", None),
        actor_email=getattr(admin, "email", None),
    )
    if not item:
        raise HTTPException(status_code=404, detail="integration_not_found")

    await ProviderRegistry.notify_change(item.domain, item.version or 1)

    return ProviderOut(
        id=item.id,
        domain=item.domain,
        provider=item.provider,
        is_enabled=item.is_enabled,
        is_active=item.is_active,
        capabilities=item.capabilities,
        version=item.version or 1,
        updated_at=item.updated_at,
        has_config=item.config_json is not None,
    )


@router.delete("/providers/{integration_id}", status_code=200)
async def delete_provider(
    integration_id: int,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> dict[str, Any]:
    _ = admin
    deleted, was_active, domain = await IntegrationProviderService.delete_provider(db, integration_id)
    if not deleted:
        return {"deleted": False}

    await ProviderRegistry.notify_change(domain or "", None)
    return {"deleted": True, "active_cleared": was_active}


@router.get("/active/{domain}", response_model=ActiveProviderOut)
async def get_active_provider(
    domain: ProviderDomain = Path(..., description="Domain"),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ActiveProviderOut:
    _ = admin
    active = await IntegrationProviderService.get_active(db, _normalize_domain(domain))
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
    dependencies=[Depends(ensure_idempotency_replay)],
)
async def set_active_provider(
    payload: SetActive,
    request: Request,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ActiveProviderOut:
    idem_key = getattr(getattr(request, "state", None), "idempotency_key", None)
    try:
        active, _, _ = await IntegrationProviderService.set_active_provider(
            db,
            domain=_normalize_domain(payload.domain),
            provider=payload.provider,
            actor_user_id=getattr(admin, "id", None),
            actor_email=getattr(admin, "email", None),
            meta={"idempotency_key": idem_key} if idem_key else None,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="integration_not_found_or_disabled")

    await ProviderRegistry.notify_change(active.domain, active.version or 1)

    if idem_key:
        await set_idempotency_result(idem_key, status_code=200, ttl_seconds=None, request=request)

    return ActiveProviderOut(
        domain=active.domain,
        provider=active.provider,
        version=active.version or 1,
        updated_at=active.updated_at,
    )


@router.get("/events", response_model=list[ProviderEventOut])
async def list_events(
    domain: ProviderDomain | None = None,
    provider_from: str | None = None,
    provider_to: str | None = None,
    actor_user_id: int | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> list[ProviderEventOut]:
    _ = admin
    items = await IntegrationProviderService.list_events(
        db,
        domain=_normalize_domain(domain),
        provider_from=provider_from,
        provider_to=provider_to,
        actor_user_id=actor_user_id,
        limit=limit,
        offset=offset,
    )
    return [
        ProviderEventOut(
            id=it.id,
            domain=it.domain,
            provider_from=it.provider_from,
            provider_to=it.provider_to,
            actor_user_id=it.actor_user_id,
            created_at=it.created_at,
            updated_at=it.updated_at,
            meta_json=it.meta_json,
        )
        for it in items
    ]


@router.post("/cache/invalidate", response_model=dict[str, str])
async def invalidate_cache(
    payload: CacheInvalidate,
    admin: Any = Depends(require_platform_admin),
) -> dict[str, str]:
    _ = admin
    domain = _normalize_domain(payload.domain)
    ProviderRegistry.invalidate(domain or None)
    await ProviderRegistry.publish_change(domain or "", None)
    return {"status": "ok", "domain": domain or "*"}


@router.get(
    "/providers/{domain}/{provider}/config",
    response_model=ProviderConfigOut,
)
async def get_provider_config(
    domain: ProviderDomain,
    provider: str,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderConfigOut:
    _ = admin
    cfg = await ProviderConfigService.get_redacted_config(db, domain=_normalize_domain(domain), provider=provider)
    _require_config_or_404(cfg)
    model = await ProviderConfigService.get_model(db, _normalize_domain(domain), provider)
    return ProviderConfigOut(
        domain=_normalize_domain(domain),
        provider=provider,
        config=cfg,
        key_id=getattr(model, "key_id", None),
        updated_at=getattr(model, "updated_at", None),
    )


@router.put(
    "/providers/{domain}/{provider}/config",
    response_model=ProviderConfigOut,
    dependencies=[Depends(ensure_idempotency_replay)],
)
async def set_provider_config(
    domain: ProviderDomain,
    provider: str,
    payload: ProviderConfigIn,
    request: Request,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderConfigOut:
    idem_key = getattr(getattr(request, "state", None), "idempotency_key", None)
    item = await ProviderConfigService.set_provider_config(
        db,
        domain=_normalize_domain(domain),
        provider=provider,
        config=payload.config,
        key_id=payload.key_id or "master",
        meta=payload.meta,
        actor_user_id=getattr(admin, "id", None),
        actor_email=getattr(admin, "email", None),
    )
    if idem_key:
        await set_idempotency_result(idem_key, status_code=200, ttl_seconds=None, request=request)
    cfg = await ProviderConfigService.get_redacted_config(db, domain=item.domain, provider=item.provider)
    return ProviderConfigOut(
        domain=item.domain,
        provider=item.provider,
        config=cfg,
        key_id=item.key_id,
        updated_at=item.updated_at,
    )


@router.post(
    "/providers/{domain}/{provider}/healthcheck",
    response_model=ProviderHealthcheckOut,
)
async def provider_healthcheck(
    domain: ProviderDomain,
    provider: str,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderHealthcheckOut:
    _ = admin
    result = await ProviderConfigService.healthcheck(
        db,
        domain=_normalize_domain(domain),
        provider=provider,
        actor_user_id=getattr(admin, "id", None),
        actor_email=getattr(admin, "email", None),
    )
    status = result.get("status") or "error"
    if status != "ok":
        return ProviderHealthcheckOut(
            status=status,
            domain=_normalize_domain(domain),
            provider=provider,
            error=result.get("error"),
        )
    return ProviderHealthcheckOut(status="ok", domain=_normalize_domain(domain), provider=provider)


# ---------------------------------------------------------------------------
# Messaging convenience endpoints
# ---------------------------------------------------------------------------


@router.get("/messaging/providers", response_model=list[ProviderOut])
async def list_messaging_providers(
    is_enabled: bool | None = None,
    is_active: bool | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> list[ProviderOut]:
    _ = admin
    items = await IntegrationProviderService.list_providers(
        db,
        domain="messaging",
        provider=None,
        is_enabled=is_enabled,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    out: list[ProviderOut] = []
    for it in items:
        out.append(
            ProviderOut(
                id=it.id,
                domain=it.domain,
                provider=it.provider,
                is_enabled=it.is_enabled,
                is_active=it.is_active,
                capabilities=it.capabilities,
                version=it.version or 1,
                created_at=it.created_at,
                updated_at=it.updated_at,
                has_config=it.config_json is not None,
            )
        )
    return out


@router.get("/messaging/config", response_model=ProviderConfigOut)
async def get_messaging_config(
    provider: str = Query(..., min_length=1, max_length=128),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderConfigOut:
    _ = admin
    cfg = await ProviderConfigService.get_redacted_config(db, domain="messaging", provider=provider)
    _require_config_or_404(cfg)
    model = await ProviderConfigService.get_model(db, "messaging", provider)
    return ProviderConfigOut(
        domain="messaging",
        provider=provider,
        config=cfg,
        key_id=getattr(model, "key_id", None),
        updated_at=getattr(model, "updated_at", None),
    )


@router.put(
    "/messaging/config",
    response_model=ProviderConfigOut,
    dependencies=[Depends(ensure_idempotency_replay)],
)
async def set_messaging_config(
    payload: MessagingConfigUpsert,
    request: Request,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderConfigOut:
    idem_key = getattr(getattr(request, "state", None), "idempotency_key", None)
    item = await ProviderConfigService.set_provider_config(
        db,
        domain="messaging",
        provider=payload.provider,
        config=payload.config,
        key_id=payload.key_id or "master",
        meta=payload.meta,
        actor_user_id=getattr(admin, "id", None),
        actor_email=getattr(admin, "email", None),
    )
    if idem_key:
        await set_idempotency_result(idem_key, status_code=200, ttl_seconds=None, request=request)
    cfg = await ProviderConfigService.get_redacted_config(db, domain=item.domain, provider=item.provider)
    return ProviderConfigOut(
        domain=item.domain,
        provider=item.provider,
        config=cfg,
        key_id=item.key_id,
        updated_at=item.updated_at,
    )


@router.get("/messaging/healthcheck", response_model=ProviderHealthcheckOut)
async def messaging_healthcheck(
    provider: str = Query(..., min_length=1, max_length=128),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderHealthcheckOut:
    _ = admin
    result = await ProviderConfigService.healthcheck(
        db,
        domain="messaging",
        provider=provider,
        actor_user_id=getattr(admin, "id", None),
        actor_email=getattr(admin, "email", None),
    )
    status = result.get("status") or "error"
    if status != "ok":
        return ProviderHealthcheckOut(status=status, domain="messaging", provider=provider, error=result.get("error"))
    return ProviderHealthcheckOut(status="ok", domain="messaging", provider=provider)


# ---------------------------------------------------------------------------
# Payments convenience endpoints
# ---------------------------------------------------------------------------


@router.get("/payments/providers", response_model=list[ProviderOut])
async def list_payment_providers(
    is_enabled: bool | None = None,
    is_active: bool | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> list[ProviderOut]:
    _ = admin
    items = await IntegrationProviderService.list_providers(
        db,
        domain="payments",
        provider=None,
        is_enabled=is_enabled,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    out: list[ProviderOut] = []
    for it in items:
        out.append(
            ProviderOut(
                id=it.id,
                domain=it.domain,
                provider=it.provider,
                is_enabled=it.is_enabled,
                is_active=it.is_active,
                capabilities=it.capabilities,
                version=it.version or 1,
                created_at=it.created_at,
                updated_at=it.updated_at,
                has_config=it.config_json is not None,
            )
        )
    return out


@router.get("/payments/config", response_model=ProviderConfigOut)
async def get_payment_config(
    provider: str = Query(..., min_length=1, max_length=128),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderConfigOut:
    _ = admin
    cfg = await ProviderConfigService.get_redacted_config(db, domain="payments", provider=provider)
    _require_config_or_404(cfg)
    model = await ProviderConfigService.get_model(db, "payments", provider)
    return ProviderConfigOut(
        domain="payments",
        provider=provider,
        config=cfg,
        key_id=getattr(model, "key_id", None),
        updated_at=getattr(model, "updated_at", None),
    )


@router.put(
    "/payments/config",
    response_model=ProviderConfigOut,
    dependencies=[Depends(ensure_idempotency_replay)],
)
async def set_payment_config(
    payload: PaymentConfigUpsert,
    request: Request,
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderConfigOut:
    idem_key = getattr(getattr(request, "state", None), "idempotency_key", None)
    item = await ProviderConfigService.set_provider_config(
        db,
        domain="payments",
        provider=payload.provider,
        config=payload.config,
        key_id=payload.key_id or "master",
        meta=payload.meta,
        actor_user_id=getattr(admin, "id", None),
    )
    if idem_key:
        await set_idempotency_result(idem_key, status_code=200, ttl_seconds=None, request=request)
    cfg = await ProviderConfigService.get_redacted_config(db, domain=item.domain, provider=item.provider)
    return ProviderConfigOut(
        domain=item.domain,
        provider=item.provider,
        config=cfg,
        key_id=item.key_id,
        updated_at=item.updated_at,
    )


@router.get("/payments/healthcheck", response_model=ProviderHealthcheckOut)
async def payment_healthcheck(
    provider: str = Query(..., min_length=1, max_length=128),
    db=Depends(get_db),
    admin: Any = Depends(require_platform_admin),
) -> ProviderHealthcheckOut:
    _ = admin
    result = await ProviderConfigService.healthcheck(
        db,
        domain="payments",
        provider=provider,
        actor_user_id=getattr(admin, "id", None),
    )
    status = result.get("status") or "error"
    if status != "ok":
        return ProviderHealthcheckOut(status=status, domain="payments", provider=provider, error=result.get("error"))
    return ProviderHealthcheckOut(status="ok", domain="payments", provider=provider)
