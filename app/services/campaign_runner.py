from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus
from app.models.integration_event import IntegrationEvent
from app.services.integration_events import record_integration_event


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _record_campaign_event(
    db: AsyncSession,
    *,
    company_id: int,
    campaign_id: int,
    status: str,
    request_id: str | None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    await record_integration_event(
        db,
        company_id=company_id,
        merchant_uid=None,
        kind="campaigns",
        status=status,
        error_code=error_code,
        error_message=error_message,
        request_id=request_id,
        meta_json={"campaign_id": campaign_id},
        commit=False,
    )


def _record_campaign_event_sync(
    db,
    *,
    company_id: int,
    campaign_id: int,
    status: str,
    request_id: str | None,
) -> None:
    event = IntegrationEvent(
        company_id=company_id,
        merchant_uid=None,
        kind="campaigns",
        status=status,
        error_code=None,
        error_message=None,
        request_id=request_id,
        meta_json={"campaign_id": campaign_id},
    )
    db.add(event)


def _due_campaigns_filter_with_retry(*, now: datetime) -> Any:
    return or_(
        Campaign.status == CampaignStatus.READY,
        Campaign.status == CampaignStatus.FAILED,
        and_(Campaign.status == CampaignStatus.SCHEDULED, Campaign.scheduled_at <= now),
    )


def _enqueue_due_campaigns_query(
    *,
    now: datetime,
    company_id: int | None,
    limit: int,
) -> tuple[list[Any], Any, int]:
    limit_value = max(1, int(limit))
    where = [_due_campaigns_filter_with_retry(now=now), Campaign.deleted_at.is_(None)]
    if company_id is not None:
        where.append(Campaign.company_id == int(company_id))
    claim_stmt = (
        select(Campaign)
        .where(*where)
        .order_by(Campaign.scheduled_at.asc().nullsfirst(), Campaign.id.asc())
        .limit(limit_value)
        .with_for_update(skip_locked=True)
    )
    return where, claim_stmt, limit_value


def _enqueue_due_campaigns_apply(campaigns: list[Campaign], *, now: datetime) -> list[Campaign]:
    queued: list[Campaign] = []
    for campaign in campaigns:
        if campaign.processing_status in (
            CampaignProcessingStatus.QUEUED,
            CampaignProcessingStatus.PROCESSING,
        ):
            continue
        if campaign.processing_status == CampaignProcessingStatus.DONE and campaign.queued_at is not None:
            continue
        campaign.processing_status = CampaignProcessingStatus.QUEUED
        campaign.queued_at = now
        queued.append(campaign)
    return queued


async def queue_campaign_run(
    db: AsyncSession,
    campaign: Campaign,
    *,
    requested_by_user_id: int | None,
    request_id: str | None,
    now: datetime | None = None,
) -> Campaign:
    now = now or _now_utc()
    if campaign.processing_status in (
        CampaignProcessingStatus.QUEUED,
        CampaignProcessingStatus.PROCESSING,
    ):
        return campaign

    campaign.processing_status = CampaignProcessingStatus.QUEUED
    campaign.queued_at = now
    campaign.started_at = None
    campaign.finished_at = None
    campaign.failed_at = None
    campaign.last_error = None
    if request_id:
        campaign.request_id = request_id
    campaign.requested_by_user_id = requested_by_user_id

    await db.commit()
    await db.refresh(campaign)
    return campaign


async def enqueue_due_campaigns(
    db: AsyncSession,
    *,
    company_id: int | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    run_id = request_id or str(uuid4())
    now = now or _now_utc()
    where, claim_stmt, limit_value = _enqueue_due_campaigns_query(
        now=now,
        company_id=company_id,
        limit=limit,
    )

    total_due = (await db.execute(select(func.count()).select_from(Campaign).where(*where))).scalar_one()
    campaigns = (await db.execute(claim_stmt)).scalars().all()

    queued = _enqueue_due_campaigns_apply(campaigns, now=now)
    queued_ids: list[int] = []
    for campaign in queued:
        await _record_campaign_event(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            status="queued",
            request_id=run_id,
        )
        queued_ids.append(campaign.id)

    if queued_ids:
        await db.commit()
    else:
        await db.rollback()

    found = min(int(total_due), limit_value)
    skipped = max(0, found - len(queued_ids))
    return {"queued": len(queued_ids), "skipped": skipped, "campaign_ids": queued_ids}


def enqueue_due_campaigns_sync(
    *,
    company_id: int | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    run_id = request_id or str(uuid4())
    now = now or _now_utc()
    where, claim_stmt, limit_value = _enqueue_due_campaigns_query(
        now=now,
        company_id=company_id,
        limit=limit,
    )

    with session_scope() as db:
        total_due = db.execute(select(func.count()).select_from(Campaign).where(*where)).scalar_one()
        campaigns = db.execute(claim_stmt).scalars().all()

        queued = _enqueue_due_campaigns_apply(campaigns, now=now)
        queued_ids: list[int] = []
        for campaign in queued:
            _record_campaign_event_sync(
                db,
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                status="queued",
                request_id=run_id,
            )
            queued_ids.append(campaign.id)

    found = min(int(total_due), limit_value)
    skipped = max(0, found - len(queued_ids))
    return {"queued": len(queued_ids), "skipped": skipped, "campaign_ids": queued_ids}


__all__ = [
    "enqueue_due_campaigns",
    "enqueue_due_campaigns_sync",
    "queue_campaign_run",
]
