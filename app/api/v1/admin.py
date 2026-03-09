from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.integrations import router as integrations_router
from app.api.v1.admin_plans import router as plans_router
from app.core.config import settings
from app.core.db import get_async_db
from app.core.dependencies import require_platform_admin
from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    SmartSellValidationError,
    _ensure_request_id,
)
from app.core.logging import audit_logger
from app.core.redis_client import get_redis
from app.core.subscriptions.catalog import get_plan_by_code
from app.core.subscriptions.plan_catalog import get_plan as get_plan_legacy
from app.core.subscriptions.plan_catalog import get_plan_display_name, normalize_plan_id
from app.core.support_workflow import build_support_triage_preview
from app.core.tenant_lifecycle import (
    TENANT_STATE_DELETE_REQUESTED,
    TENANT_STATE_PENDING_EXPORT,
    can_archive_tenant,
    can_request_delete,
    infer_current_tenant_state,
    requires_export_before_delete,
)
from app.models.billing import Subscription, WalletBalance, WalletTransaction
from app.models.campaign import (
    Campaign,
    CampaignProcessingStatus,
    CampaignStatus,
    ChannelType,
    Message,
    MessageStatus,
)
from app.models.company import Company
from app.models.invitation import InvitationToken
from app.models.kaspi_trial_grant import KaspiTrialGrant
from app.models.marketplace import KaspiStoreToken
from app.models.subscription_override import SubscriptionOverride
from app.models.user import User
from app.schemas.campaign import AdminCampaignResponse
from app.schemas.support_triage import SupportTriagePreviewIn, SupportTriagePreviewOut
from app.schemas.tenant_archive_delete import TenantArchiveDeletePreviewOut
from app.schemas.tenant_diagnostics import TenantDiagnosticsSummaryOut
from app.schemas.tenant_export import TenantExportManifestOut
from app.services.campaign_cleanup import campaign_cleanup_run
from app.services.campaign_pipeline import campaign_pipeline_tick
from app.services.campaign_runner import (
    enqueue_due_campaigns,
    should_force_requeue,
)
from app.services.campaign_runner import (
    queue_campaign_run as queue_campaign_run_service,
)
from app.services.repricing import run_reprcing_for_company
from app.services.subscriptions import activate_plan, renew_if_due
from app.services.tenant_diagnostics import get_tenant_diagnostics_summary
from app.services.tenant_export import build_tenant_export_manifest
from app.utils.tokens import generate_token, hash_token
from app.worker.campaign_processing import process_campaign_queue_once

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)
router.include_router(integrations_router)
router.include_router(plans_router)


class SubscriptionOverrideIn(BaseModel):
    active_until: datetime | None = Field(default=None)
    note: str | None = Field(default=None, max_length=2000)
    company_id: int | None = None


class SubscriptionOverrideOut(BaseModel):
    id: int
    provider: str
    company_id: int
    merchant_uid: str
    active_until: datetime | None = None
    note: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    revoked_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WalletTopupIn(BaseModel):
    companyId: int = Field(..., ge=1)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=8)
    external_reference: str | None = Field(default=None, max_length=128)
    comment: str | None = Field(default=None, max_length=500)


class WalletTopupOut(BaseModel):
    company_id: int
    wallet_id: int
    transaction_id: int
    currency: str
    balance: str
    amount: str


class SubscriptionTrialIn(BaseModel):
    companyId: int = Field(..., ge=1)
    plan: str = Field(default="pro", min_length=2, max_length=32)
    trial_days: int = Field(default=15, ge=1, le=15)


class SubscriptionKaspiTrialIn(BaseModel):
    companyId: int = Field(..., ge=1)
    merchant_uid: str = Field(..., min_length=1, max_length=128)
    plan: str = Field(default="pro", min_length=2, max_length=32)
    trial_days: int = Field(default=15, ge=1, le=15)


class KaspiTrialGrantRequest(BaseModel):
    companyId: int = Field(..., ge=1)
    merchant_uid: str = Field(..., min_length=1, max_length=128)
    plan: str = Field(default="pro", min_length=2, max_length=32)
    trial_days: int = Field(default=15, ge=1, le=15)


class KaspiTrialGrantOut(BaseModel):
    grant_id: int
    subscription_id: int
    plan: str
    active_until: datetime | None


class AdminInviteIn(BaseModel):
    companyId: int = Field(..., ge=1, validation_alias=AliasChoices("company_id", "companyId"))
    phone: str = Field(..., min_length=6, max_length=20)
    grace_days: int = Field(default=7, ge=1, le=60)
    initial_plan: str = Field(default="trial_pro")


class AdminInviteOut(BaseModel):
    invite_url: str
    otp_grace_until: datetime | None
    company_id: int


class PlatformSummaryOut(BaseModel):
    companies_total: int
    companies_active: int
    stores_with_kaspi_connected: int
    subscriptions: dict[str, Any]
    wallet: dict[str, Any]
    health: dict[str, Any]


class CompanyAdminOut(BaseModel):
    phone: str | None
    role: str
    is_active: bool


class CompanyListItem(BaseModel):
    id: int
    name: str
    bin_iin: str | None
    created_at: datetime
    is_active: bool
    kaspi_store_id: str | None
    current_plan: str | None
    plan_expires_at: datetime | None


class CompaniesPageOut(BaseModel):
    items: list[CompanyListItem]
    page: int
    size: int
    total: int


class CompanyCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    bin_iin: str | None = Field(default=None, min_length=6, max_length=32)


class CompanyDetailOut(BaseModel):
    id: int
    name: str
    bin_iin: str | None
    created_at: datetime
    is_active: bool
    kaspi_store_id: str | None
    current_plan: str | None
    plan_expires_at: datetime | None
    admins: list[CompanyAdminOut]


class SubscriptionStoreOut(BaseModel):
    company_id: int
    company_name: str
    plan: str
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    wallet_balance: Decimal


class SubscriptionSetPlanIn(BaseModel):
    plan: str = Field(..., min_length=2, max_length=32)
    reason: str = Field(..., min_length=3, max_length=500)


class SubscriptionExtendIn(BaseModel):
    days: int = Field(..., ge=1, le=365)
    reason: str = Field(..., min_length=3, max_length=500)


class OtpGraceIn(BaseModel):
    minutes: int = Field(..., ge=1, le=43200, description="OTP grace period in minutes (max 30 days)")


class OtpStatusOut(BaseModel):
    user_id: int
    otp_grace_until: datetime | None = None
    otp_setup_required: bool


@router.post(
    "/tasks/subscriptions/renew/run",
    summary="Run subscription renewal task (platform admin)",
)
async def run_subscription_renew_task(
    request: Request,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    processed = await renew_if_due(db, now=datetime.now(UTC))
    if processed:
        await db.commit()
    else:
        await db.rollback()
    rid = _ensure_request_id(request)
    return {"ok": True, "processed": processed, "request_id": rid}


class CampaignRunIn(BaseModel):
    limit: int | None = Field(default=100, ge=1)
    companyId: int | None = Field(default=None, ge=1, alias="company_id")
    dry_run: bool = False

    model_config = ConfigDict(populate_by_name=True)


def _campaign_queue_payload(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "company_id": campaign.company_id,
        "title": campaign.title,
        "processing_status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "attempts": campaign.attempts,
        "last_error": campaign.last_error,
        "request_id": campaign.request_id,
        "requested_by_user_id": campaign.requested_by_user_id,
    }


@router.post(
    "/tasks/campaigns/run",
    summary="Run campaign processing task (platform admin)",
)
async def run_campaigns_task(
    request: Request,
    payload: CampaignRunIn | None = Body(default=None),
    limit: int = Query(100, ge=1),
    dry_run: bool = Query(False),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    resolved_limit = payload.limit if payload and payload.limit is not None else limit
    resolved_company_id = None
    if payload and payload.companyId is not None:
        resolved_company_id = payload.companyId
    else:
        query_company_id = request.query_params.get("company_id") or request.query_params.get("companyId")
        if query_company_id:
            try:
                resolved_company_id = int(query_company_id)
            except ValueError:
                resolved_company_id = None
    resolved_dry_run = payload.dry_run if payload else dry_run

    if resolved_company_id is None:
        raise NotFoundError("company_id_required", code="company_id_required", http_status=400)

    rid = _ensure_request_id(request)
    if resolved_dry_run:
        return {"queued": 0, "skipped": 0, "processed": 0, "campaign_ids": []}

    enqueue_summary = await enqueue_due_campaigns(
        db,
        company_id=resolved_company_id,
        request_id=rid,
        now=datetime.now(UTC),
        limit=resolved_limit,
    )
    processed = await process_campaign_queue_once(db, limit=resolved_limit, now=datetime.now(UTC))
    return {
        "queued": enqueue_summary.get("queued", 0),
        "skipped": enqueue_summary.get("skipped", 0),
        "processed": len(processed),
        "campaign_ids": enqueue_summary.get("campaign_ids", []),
    }


@router.post(
    "/tasks/campaigns/process/run",
    summary="Run campaign pipeline tick (dev/test only)",
)
async def run_campaigns_pipeline_tick(
    request: Request,
    limit: int = Query(100, ge=1),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    if settings.is_production:
        raise NotFoundError("not_found", code="not_found", http_status=404)
    _ = _ensure_request_id(request)
    return await campaign_pipeline_tick(db, limit=limit, now=datetime.now(UTC))


@router.post(
    "/tasks/campaigns/cleanup/run",
    summary="Run campaign cleanup task (platform admin)",
)
async def run_campaigns_cleanup(
    request: Request,
    done_days: int = Query(14, ge=1, le=365),
    failed_days: int = Query(30, ge=1, le=365),
    limit: int = Query(..., ge=1, le=5000),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    request_id = _ensure_request_id(request)
    counters = await campaign_cleanup_run(
        db,
        done_days=done_days,
        failed_days=failed_days,
        limit=limit,
        now=datetime.now(UTC),
    )
    await db.commit()

    audit_logger.log_system_event(
        level="info",
        event="campaign_cleanup_run",
        message="Campaign cleanup task executed",
        meta={
            "request_id": request_id,
            "done_days": done_days,
            "failed_days": failed_days,
            "limit": limit,
            **counters,
        },
    )
    return {**counters, "request_id": request_id}


@router.post(
    "/tasks/repricing/run",
    summary="Run repricing task for a company (platform admin)",
)
async def run_repricing_task(
    request: Request,
    dry_run: bool = Query(False),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    request_id = _ensure_request_id(request)
    company_id = request.query_params.get("company_id") or request.query_params.get("companyId")
    if not company_id:
        raise NotFoundError("company_id_required", code="company_id_required", http_status=400)
    try:
        resolved_company_id = int(company_id)
    except ValueError as exc:
        raise NotFoundError("company_id_required", code="company_id_required", http_status=400) from exc
    run = await run_reprcing_for_company(
        db,
        resolved_company_id,
        triggered_by_user_id=getattr(admin, "id", None),
        dry_run=dry_run,
        request_id=request_id,
    )
    await db.commit()
    await db.refresh(run)
    return {"run_id": run.id, "status": run.status, "request_id": request_id}


@router.post(
    "/campaigns/{campaign_id}/run",
    summary="Queue a campaign run (platform admin)",
)
async def queue_campaign_run(
    request: Request,
    campaign_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    request_id = _ensure_request_id(request)
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise NotFoundError("campaign_not_found", code="campaign_not_found", http_status=404)

    if campaign.processing_status in (
        CampaignProcessingStatus.QUEUED,
        CampaignProcessingStatus.PROCESSING,
    ) and not should_force_requeue(campaign):
        return {
            "campaign_id": campaign.id,
            "status": campaign.processing_status.value,
            "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
            "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
            "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
            "last_error": campaign.last_error,
            "attempts": campaign.attempts,
            "request_id": campaign.request_id or request_id,
        }

    campaign = await queue_campaign_run_service(
        db,
        campaign,
        requested_by_user_id=getattr(admin, "id", None),
        request_id=request_id,
        now=datetime.now(UTC),
    )

    return {
        "campaign_id": campaign.id,
        "status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "last_error": campaign.last_error,
        "attempts": campaign.attempts,
        "request_id": request_id,
    }


@router.get(
    "/campaigns/queue",
    summary="List campaign processing queue (platform admin)",
)
async def list_campaign_queue(
    request: Request,
    status: str | None = Query(default=None, description="queued|processing|failed|done"),
    limit: int = Query(50, ge=1, le=200),
    companyId: int | None = Query(default=None, ge=1),
    include_deleted: bool = Query(default=False),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    _ = admin
    stmt = select(Campaign)
    if not include_deleted:
        stmt = stmt.where(Campaign.deleted_at.is_(None))
    resolved_company_id = companyId
    if resolved_company_id is None:
        query_company_id = request.query_params.get("company_id")
        if query_company_id:
            try:
                resolved_company_id = int(query_company_id)
            except ValueError:
                resolved_company_id = None
    if resolved_company_id is not None:
        stmt = stmt.where(Campaign.company_id == resolved_company_id)
    if status:
        try:
            parsed = CampaignProcessingStatus(status)
        except ValueError as exc:
            raise ConflictError(
                "invalid_processing_status",
                code="invalid_processing_status",
                http_status=400,
            ) from exc
        stmt = stmt.where(Campaign.processing_status == parsed)
    stmt = stmt.order_by(sa.nullsfirst(Campaign.queued_at.asc()), Campaign.id.asc()).limit(limit)
    campaigns = (await db.execute(stmt)).scalars().all()
    return [_campaign_queue_payload(campaign) for campaign in campaigns]


@router.post(
    "/campaigns/{campaign_id}/requeue",
    summary="Force requeue a campaign (platform admin)",
)
async def requeue_campaign(
    request: Request,
    campaign_id: int,
    force: bool = Query(default=False),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    request_id = _ensure_request_id(request)
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise NotFoundError("campaign_not_found", code="campaign_not_found", http_status=404)
    if campaign.processing_status == CampaignProcessingStatus.PROCESSING and not force:
        raise ConflictError("campaign_processing_conflict", code="campaign_processing_conflict", http_status=409)

    prev_status = campaign.processing_status.value
    prev_attempts = campaign.attempts
    prev_last_error = campaign.last_error

    campaign = await queue_campaign_run_service(
        db,
        campaign,
        requested_by_user_id=getattr(admin, "id", None),
        request_id=request_id,
        now=datetime.now(UTC),
        force=force,
    )

    payload = {
        "campaign_id": campaign.id,
        "status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "last_error": campaign.last_error,
        "attempts": campaign.attempts,
        "request_id": campaign.request_id or request_id,
    }
    if force:
        payload["warning"] = "requeued_while_processing"

    audit_logger.log_system_event(
        level="info",
        event="campaign_requeue",
        message="Campaign requeued by admin",
        meta={
            "action": "campaign_requeue",
            "campaign_id": campaign.id,
            "admin_user_id": getattr(admin, "id", None),
            "request_id": request_id,
            "force": bool(force),
            "prev_processing_status": prev_status,
            "prev_attempts": prev_attempts,
            "prev_last_error": prev_last_error,
            "new_processing_status": campaign.processing_status.value,
            "new_attempts": campaign.attempts,
            "new_last_error": campaign.last_error,
        },
    )
    return payload


@router.post(
    "/campaigns/{campaign_id}/cancel",
    summary="Cancel a campaign run (platform admin)",
)
async def cancel_campaign(
    request: Request,
    campaign_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    request_id = _ensure_request_id(request)
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise NotFoundError("campaign_not_found", code="campaign_not_found", http_status=404)

    if campaign.processing_status == CampaignProcessingStatus.DONE:
        raise ConflictError("campaign_already_done", code="campaign_already_done", http_status=409)

    if (
        campaign.processing_status == CampaignProcessingStatus.FAILED
        and (campaign.last_error or "") == "cancelled_by_admin"
    ):
        audit_logger.log_system_event(
            level="info",
            event="campaign_cancel",
            message="Campaign cancel requested (noop)",
            meta={
                "action": "campaign_cancel",
                "campaign_id": campaign.id,
                "admin_user_id": getattr(admin, "id", None),
                "request_id": request_id,
                "force": False,
                "prev_processing_status": campaign.processing_status.value,
                "prev_attempts": campaign.attempts,
                "prev_last_error": campaign.last_error,
                "new_processing_status": campaign.processing_status.value,
                "new_attempts": campaign.attempts,
                "new_last_error": campaign.last_error,
            },
        )
        return {
            "campaign_id": campaign.id,
            "status": campaign.processing_status.value,
            "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
            "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
            "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
            "last_error": campaign.last_error,
            "attempts": campaign.attempts,
            "request_id": campaign.request_id,
        }

    prev_status = campaign.processing_status.value
    prev_attempts = campaign.attempts
    prev_last_error = campaign.last_error

    now = datetime.now(UTC)
    campaign.processing_status = CampaignProcessingStatus.FAILED
    campaign.last_error = "cancelled_by_admin"
    campaign.finished_at = now
    campaign.failed_at = now
    await db.commit()
    await db.refresh(campaign)

    audit_logger.log_system_event(
        level="info",
        event="campaign_cancel",
        message="Campaign cancelled by admin",
        meta={
            "action": "campaign_cancel",
            "campaign_id": campaign.id,
            "admin_user_id": getattr(admin, "id", None),
            "request_id": request_id,
            "force": False,
            "prev_processing_status": prev_status,
            "prev_attempts": prev_attempts,
            "prev_last_error": prev_last_error,
            "new_processing_status": campaign.processing_status.value,
            "new_attempts": campaign.attempts,
            "new_last_error": campaign.last_error,
        },
    )

    return {
        "campaign_id": campaign.id,
        "status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "last_error": campaign.last_error,
        "attempts": campaign.attempts,
        "request_id": campaign.request_id or request_id,
    }


@router.get(
    "/campaigns/{campaign_id}",
    response_model=AdminCampaignResponse,
    summary="Get campaign details (platform admin)",
)
async def get_campaign_admin(
    campaign_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> AdminCampaignResponse:
    _ = admin
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise NotFoundError("campaign_not_found", code="campaign_not_found", http_status=404)
    return AdminCampaignResponse.model_validate(campaign)


@router.post(
    "/dev/seed/campaign_due",
    summary="Seed a due campaign for testing (dev/test only)",
)
async def seed_due_campaign(
    request: Request,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    if settings.is_production:
        raise NotFoundError("not_found", code="not_found", http_status=404)

    query_company_id = request.query_params.get("company_id") or request.query_params.get("companyId")
    company_id: int | None = None
    if query_company_id:
        try:
            company_id = int(query_company_id)
        except ValueError:
            raise NotFoundError("company_id_required", code="company_id_required", http_status=400)

    company: Company | None = None
    if company_id is not None:
        company = await db.get(Company, company_id)
        if not company:
            company = Company(id=company_id, name=f"Company {company_id}")
            db.add(company)
            await db.flush()
    else:
        stmt = select(Company).order_by(Company.id.asc()).limit(1)
        company = (await db.execute(stmt)).scalar_one_or_none()
        if not company:
            company = Company(name="Seed Company")
            db.add(company)
            await db.flush()

    campaign = Campaign(
        title=f"Seed due {company_id} {datetime.now(UTC).isoformat()}",
        description="seed due campaign",
        status=CampaignStatus.READY,
        scheduled_at=None,
        company_id=company.id,
    )
    db.add(campaign)
    await db.flush()

    message = Message(
        campaign_id=campaign.id,
        recipient="seed@example.com",
        content="seed",
        status=MessageStatus.PENDING,
        channel=ChannelType.EMAIL,
    )
    db.add(message)

    await db.commit()
    return {"campaign_id": campaign.id}


class SubscriptionActivateIn(BaseModel):
    companyId: int = Field(..., ge=1)
    plan: str = Field(..., min_length=2, max_length=32)


class SubscriptionAdminOut(BaseModel):
    id: int
    company_id: int
    plan: str
    status: str
    billing_cycle: str
    price: Decimal
    currency: str
    started_at: datetime | None
    period_start: datetime | None
    period_end: datetime | None
    next_billing_date: datetime | None
    grace_until: datetime | None
    billing_anchor_day: int | None

    model_config = ConfigDict(from_attributes=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _frontend_base_url() -> str:
    return str(getattr(settings, "FRONTEND_URL", "http://localhost:3000") or "").rstrip("/")


def _normalize_phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _normalize_initial_plan(value: str | None) -> str:
    raw = (value or "trial_pro").strip().lower()
    if raw == "trial_pro":
        return "trial_pro"
    if raw == "free":
        return "start"
    if raw == "pro":
        return "pro"
    return raw


def _ceil_to_midnight_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


async def _grant_trial_subscription(
    db: AsyncSession,
    *,
    company_id: int,
    plan_code: str,
    trial_days: int,
    now: datetime | None = None,
) -> Subscription:
    plan_id = normalize_plan_id(plan_code, default=plan_code) or plan_code
    plan = await get_plan_by_code(db, plan_id)
    plan_price = None
    plan_currency = None
    if plan is None:
        legacy = get_plan_legacy(normalize_plan_id(plan_id, default=None), default=None)
        if legacy is None:
            raise AuthorizationError("plan_not_found", code="plan_not_found", http_status=400)
        plan_id = legacy.plan_id
        plan_price = legacy.price
        plan_currency = legacy.currency
    else:
        plan_price = plan.price
        plan_currency = plan.currency

    now = now or _utc_now()
    period_end = now + timedelta(days=trial_days)
    grace_until = _ceil_to_midnight_utc(period_end + timedelta(days=3))

    stmt = select(Subscription).where(Subscription.company_id == company_id).where(Subscription.deleted_at.is_(None))
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if sub is None:
        sub = Subscription(company_id=company_id)
        db.add(sub)

    sub.plan = plan_id
    sub.status = "trialing"
    sub.billing_cycle = "monthly"
    sub.price = Decimal(str(plan_price or 0))
    sub.currency = plan_currency or "KZT"
    sub.started_at = now
    sub.period_start = now
    sub.period_end = period_end
    sub.next_billing_date = period_end
    sub.billing_anchor_day = now.day
    sub.grace_until = grace_until
    sub.expires_at = period_end
    sub.trial_used = True

    await db.flush()
    return sub


async def _grant_kaspi_trial(
    db: AsyncSession,
    *,
    company: Company,
    merchant_uid: str,
    plan_code: str,
    trial_days: int,
) -> tuple[KaspiTrialGrant, Subscription]:
    merchant_uid = merchant_uid.strip()
    if not merchant_uid:
        raise AuthorizationError("merchant_uid_required", code="merchant_uid_required", http_status=400)

    token_exists = (
        await db.execute(
            select(sa.literal(True))
            .select_from(KaspiStoreToken)
            .where(sa.func.lower(KaspiStoreToken.store_name) == sa.func.lower(sa.literal(merchant_uid)))
        )
    ).scalar_one_or_none()

    linked_company = (company.kaspi_store_id or "").strip() == merchant_uid
    if not linked_company and not token_exists:
        raise AuthorizationError(
            "merchant_uid_not_linked",
            code="merchant_uid_not_linked",
            http_status=400,
        )

    now = _utc_now()
    trial_ends_at = now + timedelta(days=trial_days)
    grant = KaspiTrialGrant(
        provider="kaspi",
        merchant_uid=merchant_uid,
        company_id=company.id,
        trial_ends_at=trial_ends_at,
        status="active",
        granted_at=now,
    )
    db.add(grant)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AuthorizationError(
            "trial_already_used_for_merchant_uid",
            code="trial_already_used_for_merchant_uid",
            http_status=409,
            extra={"merchant_uid": merchant_uid},
        )

    sub = await _grant_trial_subscription(
        db,
        company_id=company.id,
        plan_code=plan_code,
        trial_days=trial_days,
        now=now,
    )
    grant.subscription_id = sub.id
    return grant, sub


async def _resolve_company(
    *,
    db: AsyncSession,
    company_id: int | None,
) -> Company:
    if company_id is None:
        raise NotFoundError("company_id_required", code="company_id_required", http_status=400)
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)
    return company


async def _subscription_summary(db: AsyncSession) -> dict[str, Any]:
    stmt = (
        select(
            func.count(Subscription.id),
            func.sum(sa.case((Subscription.status == "trialing", 1), else_=0)),
            func.sum(sa.case((Subscription.plan == "pro", 1), else_=0)),
            func.sum(sa.case((Subscription.plan.in_(["start", "basic"]), 1), else_=0)),
        )
        .where(Subscription.deleted_at.is_(None))
        .select_from(Subscription)
    )
    row = (await db.execute(stmt)).one()
    total, trial_count, pro_count, free_count = (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
    return {
        "total": total,
        "by_plan": {
            "free": free_count,
            "trial": trial_count,
            "pro": pro_count,
        },
    }


async def _company_plan_info(db: AsyncSession, company: Company) -> tuple[str | None, datetime | None]:
    stmt = (
        select(Subscription)
        .where(Subscription.company_id == company.id, Subscription.deleted_at.is_(None))
        .order_by(Subscription.id.desc())
        .limit(1)
    )
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if sub:
        plan_name = get_plan_display_name(sub.plan, default=sub.plan or "start")
        if (sub.status or "").lower() == "trialing":
            plan_name = f"Trial {plan_name}"
        plan_expires = sub.period_end or sub.expires_at
        return plan_name, plan_expires
    plan_name = get_plan_display_name(company.subscription_plan, default=company.subscription_plan)
    return plan_name, company.subscription_expires_at


@router.post(
    "/invites",
    response_model=AdminInviteOut,
    summary="Invite store admin with OTP grace (platform admin)",
)
async def create_admin_invite(
    payload: AdminInviteIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> AdminInviteOut:
    company = await _resolve_company(db=db, company_id=payload.companyId)
    phone = _normalize_phone(payload.phone)
    if not phone:
        raise AuthorizationError("invalid_phone", code="invalid_phone", http_status=400)

    variants = [phone, f"+{phone}"]
    res = await db.execute(select(User).where(User.phone.in_(variants)))
    user = res.scalars().first()
    if not user:
        user = User(company_id=company.id, phone=phone, role="admin", is_active=True, is_verified=False)
        db.add(user)
    else:
        user.company_id = company.id
        user.phone = phone
        user.role = "admin"
        user.is_active = True

    now = datetime.now(UTC).replace(tzinfo=None)
    user.otp_grace_until = now + timedelta(days=payload.grace_days)
    user.otp_setup_required = False

    initial_plan = _normalize_initial_plan(payload.initial_plan)
    if initial_plan == "trial_pro":
        await _grant_trial_subscription(db, company_id=company.id, plan_code="pro", trial_days=15)
    elif initial_plan == "pro":
        await activate_plan(db, company_id=company.id, plan_code="pro")
    elif initial_plan == "start":
        stmt = select(Subscription).where(Subscription.company_id == company.id, Subscription.deleted_at.is_(None))
        sub = (await db.execute(stmt)).scalar_one_or_none()
        if sub is None:
            sub = Subscription(
                company_id=company.id,
                plan="start",
                status="active",
                billing_cycle="monthly",
                price=Decimal("0"),
                currency="KZT",
                started_at=now,
                period_start=now,
                period_end=None,
                next_billing_date=None,
            )
            db.add(sub)

    token = generate_token()
    token_hash = hash_token(token, secret=getattr(settings, "INVITE_TOKEN_SECRET", None))
    invite = InvitationToken.build(
        company_id=company.id,
        role="admin",
        phone=phone,
        token_hash=token_hash,
        ttl_hours=72,
        created_by_user_id=admin.id,
    )
    db.add(invite)
    await db.commit()

    invite_url = f"{_frontend_base_url()}/invite?token={token}"
    return AdminInviteOut(invite_url=invite_url, otp_grace_until=user.otp_grace_until, company_id=company.id)


@router.get(
    "/platform/summary",
    response_model=PlatformSummaryOut,
    summary="Platform summary (platform admin)",
)
async def platform_summary(
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> PlatformSummaryOut:
    _ = admin
    companies_total = (await db.execute(select(func.count()).select_from(Company))).scalar_one() or 0
    companies_active = (
        await db.execute(select(func.count()).select_from(Company).where(Company.is_active.is_(True)))
    ).scalar_one() or 0

    store_token_exists = sa.exists(
        select(sa.literal(True))
        .select_from(KaspiStoreToken)
        .where(sa.func.lower(KaspiStoreToken.store_name) == sa.func.lower(Company.kaspi_store_id))
    )
    stores_with_kaspi_connected = (
        await db.execute(
            select(func.count())
            .select_from(Company)
            .where(Company.kaspi_store_id.is_not(None))
            .where(store_token_exists)
        )
    ).scalar_one() or 0

    subscriptions = await _subscription_summary(db)

    wallet_row = (
        await db.execute(
            select(func.sum(WalletBalance.balance), func.count(WalletBalance.id)).select_from(WalletBalance)
        )
    ).one()
    total_balance = wallet_row[0] or Decimal("0")
    active_wallets = int(wallet_row[1] or 0)

    db_ok = True
    try:
        await db.execute(select(sa.literal(True)))
    except Exception:
        db_ok = False

    redis_ok = False
    redis_client = get_redis()
    if redis_client is not None:
        try:
            redis_ok = bool(await redis_client.ping())
        except Exception:
            redis_ok = False

    worker_ok = os.getenv("SMARTSELL_BACKGROUND_TASKS", "0").strip().lower() in {"1", "true", "yes", "on"}

    return PlatformSummaryOut(
        companies_total=int(companies_total),
        companies_active=int(companies_active),
        stores_with_kaspi_connected=int(stores_with_kaspi_connected),
        subscriptions=subscriptions,
        wallet={"total_balance": total_balance, "active_wallets": active_wallets},
        health={"db_ok": db_ok, "redis_ok": redis_ok, "worker_ok": worker_ok},
    )


@router.get(
    "/companies",
    response_model=CompaniesPageOut,
    summary="List companies (platform admin)",
)
async def list_companies(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> CompaniesPageOut:
    _ = admin
    stmt = select(Company)
    count_stmt = select(func.count()).select_from(Company)
    if q:
        like = f"%{q.strip()}%"
        clause = sa.or_(Company.name.ilike(like), Company.bin_iin.ilike(like))
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    total = (await db.execute(count_stmt)).scalar_one() or 0
    offset = (page - 1) * size
    rows = (await db.execute(stmt.order_by(Company.created_at.desc()).offset(offset).limit(size))).scalars().all()

    items: list[CompanyListItem] = []
    for company in rows:
        plan_name, plan_expires = await _company_plan_info(db, company)
        items.append(
            CompanyListItem(
                id=company.id,
                name=company.name,
                bin_iin=company.bin_iin,
                created_at=company.created_at,
                is_active=bool(company.is_active),
                kaspi_store_id=company.kaspi_store_id,
                current_plan=plan_name,
                plan_expires_at=plan_expires,
            )
        )

    return CompaniesPageOut(items=items, page=page, size=size, total=int(total))


@router.get(
    "/companies/{company_id}",
    response_model=CompanyDetailOut,
    summary="Company details (platform admin)",
)
async def get_company_detail(
    company_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> CompanyDetailOut:
    _ = admin
    company = await _resolve_company(db=db, company_id=company_id)
    plan_name, plan_expires = await _company_plan_info(db, company)

    admin_roles = {"admin", "manager"}
    users = (
        (
            await db.execute(
                select(User).where(User.company_id == company.id, User.role.in_(admin_roles)).order_by(User.id.desc())
            )
        )
        .scalars()
        .all()
    )
    admins = [CompanyAdminOut(phone=u.phone, role=u.role, is_active=bool(u.is_active)) for u in users]

    return CompanyDetailOut(
        id=company.id,
        name=company.name,
        bin_iin=company.bin_iin,
        created_at=company.created_at,
        is_active=bool(company.is_active),
        kaspi_store_id=company.kaspi_store_id,
        current_plan=plan_name,
        plan_expires_at=plan_expires,
        admins=admins,
    )


@router.get(
    "/tenants/{company_id}/diagnostics",
    response_model=TenantDiagnosticsSummaryOut,
    summary="Tenant diagnostics summary (platform admin)",
)
async def get_tenant_diagnostics_summary_endpoint(
    company_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> TenantDiagnosticsSummaryOut:
    _ = admin
    return await get_tenant_diagnostics_summary(db, company_id=company_id)


@router.get(
    "/tenants/{company_id}/export",
    response_model=TenantExportManifestOut,
    summary="Tenant export manifest preview (platform admin)",
)
async def admin_tenant_export_manifest(
    company_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> TenantExportManifestOut:
    exported_by = str(getattr(admin, "phone", None) or getattr(admin, "id", "platform_admin"))
    return await build_tenant_export_manifest(
        db,
        company_id=company_id,
        exported_by=exported_by,
    )


@router.get(
    "/tenants/{company_id}/archive-delete-preview",
    response_model=TenantArchiveDeletePreviewOut,
    summary="Tenant archive/delete preview (platform admin, no side effects)",
)
async def admin_tenant_archive_delete_preview(
    company_id: int,
    action: Literal["archive", "delete"] = Query(...),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> TenantArchiveDeletePreviewOut:
    _ = admin
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    current_state = infer_current_tenant_state(company)

    if action == "archive":
        allowed = can_archive_tenant(current_state=current_state)
        next_state = "archived" if allowed else current_state
        required = ["platform_admin_reason", "evidence_trail"]
        warnings: list[str] = []
        if current_state == "archived":
            warnings.append("tenant_already_archived")
        return TenantArchiveDeletePreviewOut(
            company_id=company_id,
            current_state=current_state,
            requested_action=action,
            allowed=allowed,
            required_before_action=required,
            warnings=warnings,
            next_state=next_state,
            destructive_delete_supported=False,
        )

    export_required = requires_export_before_delete()
    has_export_manifest_reference = False
    allowed = can_request_delete(
        current_state=current_state,
        has_export_manifest_reference=has_export_manifest_reference,
    )
    warnings = ["delete_is_policy_only_no_destructive_delete"]
    required = ["platform_admin_reason", "evidence_trail"]
    if export_required:
        required.insert(0, "export_manifest_reference")
        if not has_export_manifest_reference:
            warnings.append("export_before_delete_required")

    if export_required and not has_export_manifest_reference:
        next_state = TENANT_STATE_PENDING_EXPORT
    elif allowed:
        next_state = TENANT_STATE_DELETE_REQUESTED
    else:
        next_state = current_state

    return TenantArchiveDeletePreviewOut(
        company_id=company_id,
        current_state=current_state,
        requested_action=action,
        allowed=allowed,
        required_before_action=required,
        warnings=warnings,
        next_state=next_state,
        destructive_delete_supported=False,
    )


@router.post(
    "/tenants/{company_id}/support-triage-preview",
    response_model=SupportTriagePreviewOut,
    summary="Support triage preview (platform admin, no side effects)",
)
async def admin_support_triage_preview(
    company_id: int,
    payload: SupportTriagePreviewIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SupportTriagePreviewOut:
    _ = admin
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    try:
        preview = build_support_triage_preview(
            company_id=company_id,
            severity=payload.severity,
            area=payload.area,
            issue_summary=payload.issue_summary,
            latest_request_id=payload.latest_request_id,
        )
    except ValueError as exc:
        code = str(exc)
        raise SmartSellValidationError("Invalid support triage payload", code=code, http_status=422) from exc

    return SupportTriagePreviewOut(
        **preview,
        diagnostics_endpoint=f"/api/v1/admin/tenants/{company_id}/diagnostics",
        export_endpoint=f"/api/v1/admin/tenants/{company_id}/export",
        archive_delete_preview_endpoint=f"/api/v1/admin/tenants/{company_id}/archive-delete-preview?action=archive",
    )


@router.post(
    "/companies",
    response_model=CompanyListItem,
    summary="Create company (platform admin)",
)
async def create_company(
    payload: CompanyCreateIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> CompanyListItem:
    _ = admin
    company = Company(name=payload.name.strip(), bin_iin=(payload.bin_iin or None))
    db.add(company)
    await db.commit()
    await db.refresh(company)
    plan_name, plan_expires = await _company_plan_info(db, company)
    return CompanyListItem(
        id=company.id,
        name=company.name,
        bin_iin=company.bin_iin,
        created_at=company.created_at,
        is_active=bool(company.is_active),
        kaspi_store_id=company.kaspi_store_id,
        current_plan=plan_name,
        plan_expires_at=plan_expires,
    )


@router.get(
    "/subscriptions/stores",
    response_model=list[SubscriptionStoreOut],
    summary="List subscriptions by store (platform admin)",
)
async def list_subscriptions_by_store(
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> list[SubscriptionStoreOut]:
    _ = admin
    now = datetime.now(UTC)
    stmt = (
        select(Subscription, Company, WalletBalance)
        .join(Company, Company.id == Subscription.company_id)
        .outerjoin(WalletBalance, WalletBalance.company_id == Company.id)
        .where(Subscription.deleted_at.is_(None))
    )
    rows = (await db.execute(stmt)).all()
    results: list[SubscriptionStoreOut] = []
    for sub, company, wallet in rows:
        status_raw = (sub.status or "").lower()
        if status_raw == "trialing":
            status = "trial"
        elif status_raw == "active":
            status = "active"
        elif sub.period_end and sub.period_end >= now:
            status = "active"
        else:
            status = "expired"
        results.append(
            SubscriptionStoreOut(
                company_id=company.id,
                company_name=company.name,
                plan=sub.plan,
                status=status,
                current_period_start=sub.period_start,
                current_period_end=sub.period_end,
                wallet_balance=(wallet.balance if wallet else Decimal("0")),
            )
        )
    return results


@router.post(
    "/subscriptions/{company_id}/set-plan",
    response_model=SubscriptionAdminOut,
    summary="Set subscription plan (platform admin)",
)
async def set_subscription_plan(
    company_id: int,
    payload: SubscriptionSetPlanIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    company = await _resolve_company(db=db, company_id=company_id)
    plan_code = normalize_plan_id(payload.plan, default=payload.plan) or payload.plan
    if plan_code == "free":
        plan_code = "start"
    sub = await activate_plan(db, company_id=company.id, plan_code=plan_code)
    await db.commit()
    await db.refresh(sub)

    audit_logger.log_system_event(
        level="info",
        event="subscription_plan_set",
        message="Subscription plan set by platform admin",
        meta={"company_id": company.id, "plan": sub.plan, "reason": payload.reason},
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.post(
    "/subscriptions/{company_id}/extend",
    response_model=SubscriptionAdminOut,
    summary="Extend subscription (platform admin)",
)
async def extend_subscription(
    company_id: int,
    payload: SubscriptionExtendIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    _ = await _resolve_company(db=db, company_id=company_id)
    stmt = (
        select(Subscription)
        .where(Subscription.company_id == company_id, Subscription.deleted_at.is_(None))
        .order_by(Subscription.id.desc())
        .limit(1)
    )
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if not sub:
        raise NotFoundError("subscription_not_found", code="subscription_not_found", http_status=404)

    base_end = sub.period_end or datetime.now(UTC)
    new_end = base_end + timedelta(days=payload.days)
    sub.period_end = new_end
    sub.next_billing_date = new_end
    if sub.expires_at:
        sub.expires_at = sub.expires_at + timedelta(days=payload.days)

    await db.commit()
    await db.refresh(sub)

    audit_logger.log_system_event(
        level="info",
        event="subscription_extended",
        message="Subscription extended by platform admin",
        meta={"company_id": company_id, "days": payload.days, "reason": payload.reason},
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.get(
    "/users/{user_id}/otp/status",
    response_model=OtpStatusOut,
    summary="Get OTP status for user (platform admin)",
)
async def get_user_otp_status(
    user_id: int,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> OtpStatusOut:
    _ = admin
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError("user_not_found", code="user_not_found", http_status=404)
    return OtpStatusOut(
        user_id=user.id, otp_grace_until=user.otp_grace_until, otp_setup_required=bool(user.otp_setup_required)
    )


@router.post(
    "/users/{user_id}/otp/grace",
    response_model=OtpStatusOut,
    summary="Set OTP grace period for user (platform admin)",
)
async def set_user_otp_grace(
    user_id: int,
    payload: OtpGraceIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> OtpStatusOut:
    _ = admin
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError("user_not_found", code="user_not_found", http_status=404)

    now = datetime.now(UTC).replace(tzinfo=None)
    user.otp_grace_until = now + timedelta(minutes=payload.minutes)
    user.otp_setup_required = False
    await db.commit()
    await db.refresh(user)

    audit_logger.log_system_event(
        level="info",
        event="otp_grace_set",
        message="OTP grace updated by platform admin",
        meta={
            "user_id": user.id,
            "otp_grace_until": user.otp_grace_until.isoformat() if user.otp_grace_until else None,
        },
    )
    return OtpStatusOut(
        user_id=user.id,
        otp_grace_until=user.otp_grace_until,
        otp_setup_required=bool(user.otp_setup_required),
    )


@router.post(
    "/wallet/topup",
    response_model=WalletTopupOut,
    summary="Manual company wallet top-up (platform admin)",
)
async def manual_wallet_topup(
    payload: WalletTopupIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> WalletTopupOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    wallet = await WalletBalance.get_for_company_async(
        db,
        payload.companyId,
        create_if_missing=True,
        currency=payload.currency,
    )
    if (wallet.currency or "").upper() != payload.currency.upper():
        raise AuthorizationError("wallet_currency_mismatch", code="wallet_currency_mismatch", http_status=400)

    amount = Decimal(str(payload.amount))
    if payload.external_reference:
        existing_stmt = select(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet.id,
            WalletTransaction.client_request_id == payload.external_reference,
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            return WalletTopupOut(
                company_id=payload.companyId,
                wallet_id=wallet.id,
                transaction_id=existing.id,
                currency=wallet.currency,
                balance=str(existing.balance_after),
                amount=str(existing.amount),
            )
    before = wallet.balance or Decimal("0")
    after = before + amount
    wallet.balance = after
    trx = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type="manual_topup",
        amount=amount,
        balance_before=before,
        balance_after=after,
        description=payload.comment or "manual_topup",
        reference_type="manual_topup",
        client_request_id=payload.external_reference,
        extra_data=json.dumps(
            {
                "external_reference": payload.external_reference,
                "comment": payload.comment,
            },
            ensure_ascii=False,
        ),
    )
    db.add(trx)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if payload.external_reference:
            existing_stmt = select(WalletTransaction).where(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.client_request_id == payload.external_reference,
            )
            existing = (await db.execute(existing_stmt)).scalar_one_or_none()
            if existing:
                return WalletTopupOut(
                    company_id=payload.companyId,
                    wallet_id=wallet.id,
                    transaction_id=existing.id,
                    currency=wallet.currency,
                    balance=str(existing.balance_after),
                    amount=str(existing.amount),
                )
        raise

    audit_logger.log_system_event(
        level="info",
        event="wallet_manual_topup",
        message="Wallet credited manually",
        meta={
            "company_id": payload.companyId,
            "wallet_id": wallet.id,
            "amount": str(amount),
            "currency": payload.currency,
            "transaction_id": trx.id,
        },
    )

    return WalletTopupOut(
        company_id=payload.companyId,
        wallet_id=wallet.id,
        transaction_id=trx.id,
        currency=wallet.currency,
        balance=str(wallet.balance),
        amount=str(amount),
    )


@router.post(
    "/subscriptions/trial",
    response_model=SubscriptionAdminOut,
    summary="Grant trial subscription (platform admin)",
)
async def grant_trial_subscription(
    payload: SubscriptionTrialIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    sub = await _grant_trial_subscription(
        db,
        company_id=payload.companyId,
        plan_code=payload.plan,
        trial_days=payload.trial_days,
    )
    await db.commit()
    await db.refresh(sub)

    audit_logger.log_system_event(
        level="info",
        event="subscription_trial_granted",
        message="Subscription trial granted",
        meta={
            "company_id": payload.companyId,
            "plan": sub.plan,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
            "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
        },
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.post(
    "/subscriptions/activate",
    response_model=SubscriptionAdminOut,
    summary="Activate subscription from wallet (platform admin)",
)
async def activate_subscription_admin(
    payload: SubscriptionActivateIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    try:
        sub = await activate_plan(db, company_id=payload.companyId, plan_code=payload.plan)
        await db.commit()
        await db.refresh(sub)
    except ValueError as exc:
        msg = str(exc).lower()
        if "insufficient" in msg:
            raise AuthorizationError(
                "insufficient_wallet_balance",
                code="insufficient_wallet_balance",
                http_status=400,
            )
        if "unknown plan" in msg:
            raise AuthorizationError("plan_not_found", code="plan_not_found", http_status=400)
        if "currency" in msg:
            raise AuthorizationError("wallet_currency_mismatch", code="wallet_currency_mismatch", http_status=400)
        raise

    audit_logger.log_system_event(
        level="info",
        event="subscription_activated_admin",
        message="Subscription activated by admin",
        meta={
            "company_id": payload.companyId,
            "plan": sub.plan,
        },
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.post(
    "/subscriptions/trial/kaspi",
    response_model=KaspiTrialGrantOut,
    summary="Grant Kaspi trial subscription (platform admin)",
)
async def grant_kaspi_trial_subscription(
    payload: KaspiTrialGrantRequest,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> KaspiTrialGrantOut:
    _ = admin

    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    grant, sub = await _grant_kaspi_trial(
        db,
        company=company,
        merchant_uid=payload.merchant_uid,
        plan_code=payload.plan,
        trial_days=payload.trial_days,
    )

    await db.commit()
    await db.refresh(grant)
    await db.refresh(sub)

    audit_logger.log_system_event(
        level="info",
        event="subscription_trial_granted",
        message="Kaspi subscription trial granted",
        meta={
            "company_id": company.id,
            "plan": sub.plan,
            "merchant_uid": payload.merchant_uid,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
            "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
        },
    )

    return KaspiTrialGrantOut(
        grant_id=grant.id,
        subscription_id=sub.id,
        plan=sub.plan,
        active_until=sub.period_end,
    )


@router.get(
    "/subscription-overrides",
    response_model=list[SubscriptionOverrideOut],
    summary="List subscription overrides",
)
async def list_subscription_overrides(
    provider: str = Query("kaspi"),
    companyId: int | None = Query(None),
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> list[SubscriptionOverrideOut]:
    _ = current_user
    company = await _resolve_company(db=db, company_id=companyId)
    stmt = sa.select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company.id,
        SubscriptionOverride.provider == provider,
    )
    rows = (await db.execute(stmt.order_by(SubscriptionOverride.created_at.desc()))).scalars().all()
    return [SubscriptionOverrideOut.model_validate(row) for row in rows]


@router.put(
    "/subscription-overrides/kaspi/{merchant_uid}",
    response_model=SubscriptionOverrideOut,
    summary="Upsert subscription override (Kaspi)",
)
async def upsert_subscription_override_kaspi(
    merchant_uid: str,
    payload: SubscriptionOverrideIn,
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionOverrideOut:
    company = await _resolve_company(db=db, company_id=payload.company_id)
    merchant = merchant_uid.strip()
    stmt = sa.select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company.id,
        SubscriptionOverride.provider == "kaspi",
        SubscriptionOverride.merchant_uid == merchant,
    )
    row = (await db.execute(stmt)).scalars().first()
    if row:
        row.active_until = payload.active_until
        row.note = payload.note
        row.revoked_at = None
    else:
        row = SubscriptionOverride(
            provider="kaspi",
            company_id=company.id,
            merchant_uid=merchant,
            active_until=payload.active_until,
            note=payload.note,
            created_by_user_id=current_user.id,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return SubscriptionOverrideOut.model_validate(row)


@router.delete(
    "/subscription-overrides/kaspi/{merchant_uid}",
    summary="Revoke subscription override (Kaspi)",
)
async def revoke_subscription_override_kaspi(
    merchant_uid: str,
    companyId: int | None = Query(None),
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    _ = current_user
    company = await _resolve_company(db=db, company_id=companyId)
    stmt = sa.select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company.id,
        SubscriptionOverride.provider == "kaspi",
        SubscriptionOverride.merchant_uid == merchant_uid,
    )
    row = (await db.execute(stmt)).scalars().first()
    if not row:
        raise NotFoundError("override_not_found", code="override_not_found", http_status=404)
    row.revoked_at = _utc_now()
    await db.commit()
    return {"status": "revoked", "merchant_uid": merchant_uid}
