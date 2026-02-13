from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_maker, session_scope
from app.core.logging import bound_context, get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus, Message, MessageStatus
from app.models.integration_event import IntegrationEvent
from app.services.integration_events import record_integration_event
from app.services.messaging_providers import MessagingProviderResolver
from app.services.retry_policy import RetryPolicy

logger = get_logger(__name__)

_DEFAULT_RETRY = RetryPolicy(timeout_seconds=8.0, retries=2, backoff_seconds=0.5)
_ERROR_MESSAGE_LIMIT = 500


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _log_failure_metric(*, campaign_id: int, company_id: int, error_code: str) -> None:
    logger.warning(
        "metric=campaign_job_failures", extra={"campaign_id": campaign_id, "company_id": company_id, "code": error_code}
    )


def _truncate_error(message: str | None, limit: int = _ERROR_MESSAGE_LIMIT) -> str | None:
    if not message:
        return None
    cleaned = message.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


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


def _due_campaigns_filter(*, now: datetime) -> Any:
    return or_(
        Campaign.status == CampaignStatus.READY,
        and_(Campaign.status == CampaignStatus.SCHEDULED, Campaign.scheduled_at <= now),
    )


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


async def _send_message(
    provider,
    message: Message,
    *,
    request_id: str,
    retry_policy: RetryPolicy,
) -> None:
    metadata = {
        "campaign_id": message.campaign_id,
        "message_id": message.id,
        "request_id": request_id,
    }
    await retry_policy.run(provider.send_message, message.recipient, message.content, metadata)
    message.mark_sent(provider_id=None)


async def _process_campaign(
    db: AsyncSession,
    campaign: Campaign,
    *,
    request_id: str,
    retry_policy: RetryPolicy,
) -> CampaignStatus:
    campaign.status = CampaignStatus.RUNNING
    campaign.error_code = None
    campaign.error_message = None
    campaign.request_id = request_id
    await db.flush()

    messages = (
        (
            await db.execute(
                select(Message).where(
                    Message.campaign_id == campaign.id,
                    Message.status == MessageStatus.PENDING,
                    Message.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    if not messages:
        campaign.status = CampaignStatus.SUCCESS
        await db.flush()
        return campaign.status

    try:
        provider = await MessagingProviderResolver.resolve(db, domain="messaging")
    except ProviderNotConfiguredError as exc:
        campaign.status = CampaignStatus.FAILED
        campaign.error_code = exc.code
        campaign.error_message = "messaging provider not configured"
        await db.flush()
        _log_failure_metric(campaign_id=campaign.id, company_id=campaign.company_id, error_code=exc.code)
        return campaign.status

    failures = 0
    for message in messages:
        try:
            await _send_message(provider, message, request_id=request_id, retry_policy=retry_policy)
        except ProviderNotConfiguredError as exc:
            message.mark_failed(reason=str(exc), error_code=exc.code)
            failures += 1
            campaign.error_code = exc.code
            campaign.error_message = str(exc)
            _log_failure_metric(campaign_id=campaign.id, company_id=campaign.company_id, error_code=exc.code)
            if settings.is_production:
                break
        except Exception as exc:
            message.mark_failed(reason=str(exc), error_code="message_send_failed")
            failures += 1
            campaign.error_code = "message_send_failed"
            campaign.error_message = str(exc)
            _log_failure_metric(
                campaign_id=campaign.id, company_id=campaign.company_id, error_code="message_send_failed"
            )
            if settings.is_production:
                break

    await campaign.refresh_counters_async(db)
    if failures:
        campaign.status = CampaignStatus.FAILED
    else:
        campaign.status = CampaignStatus.SUCCESS
    await db.flush()
    return campaign.status


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


# Deprecated: use enqueue_due_campaigns + campaign_processing.
async def run_campaigns(
    db: AsyncSession,
    *,
    company_id: int | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    run_id = request_id or str(uuid4())
    now = now or _now_utc()

    where = [_due_campaigns_filter(now=now), Campaign.deleted_at.is_(None)]
    if company_id is not None:
        where.append(Campaign.company_id == int(company_id))

    rows = (await db.execute(select(Campaign).where(*where).limit(limit))).scalars().all()
    results: list[dict[str, Any]] = []

    for campaign in rows:
        with bound_context(request_id=run_id, tenant=str(campaign.company_id)):
            try:
                status = await _process_campaign(db, campaign, request_id=run_id, retry_policy=_DEFAULT_RETRY)
                results.append({"campaign_id": campaign.id, "status": status.value})
            except Exception as exc:  # pragma: no cover - defensive
                campaign.status = CampaignStatus.FAILED
                campaign.error_code = "campaign_runner_failed"
                campaign.error_message = str(exc)
                campaign.request_id = run_id
                await db.flush()
                _log_failure_metric(
                    campaign_id=campaign.id,
                    company_id=campaign.company_id,
                    error_code="campaign_runner_failed",
                )
                results.append({"campaign_id": campaign.id, "status": CampaignStatus.FAILED.value})

    await db.commit()
    return results


# Deprecated: use enqueue_due_campaigns + campaign_processing.
async def run_campaigns_with_claim(
    db: AsyncSession,
    *,
    company_id: int | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
    limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_id = request_id or str(uuid4())
    now = now or _now_utc()

    where = [_due_campaigns_filter(now=now), Campaign.deleted_at.is_(None)]
    if company_id is not None:
        where.append(Campaign.company_id == int(company_id))

    details: list[dict[str, Any]] = []
    total_due = (await db.execute(select(func.count()).select_from(Campaign).where(*where))).scalar_one()
    limit_value = max(1, int(limit))
    found = min(int(total_due), limit_value)

    if dry_run:
        rows = (
            await db.execute(
                select(Campaign.id, Campaign.company_id, Campaign.status)
                .where(*where)
                .order_by(Campaign.scheduled_at.asc().nullsfirst(), Campaign.id.asc())
                .limit(limit_value)
            )
        ).all()
        for row in rows:
            details.append(
                {
                    "campaign_id": row.id,
                    "company_id": row.company_id,
                    "status_before": row.status.value,
                    "status_after": None,
                    "reason": "dry_run",
                }
            )
        return {
            "found": len(rows),
            "started": 0,
            "skipped": len(rows),
            "details": details,
            "request_id": run_id,
        }

    claim_stmt = (
        select(Campaign)
        .where(*where)
        .order_by(Campaign.scheduled_at.asc().nullsfirst(), Campaign.id.asc())
        .limit(limit_value)
        .with_for_update(skip_locked=True)
    )
    campaigns = (await db.execute(claim_stmt)).scalars().all()

    for campaign in campaigns:
        status_before = campaign.status
        with bound_context(request_id=run_id, tenant=str(campaign.company_id)):
            try:
                status_after = await _process_campaign(db, campaign, request_id=run_id, retry_policy=_DEFAULT_RETRY)
                reason = campaign.error_code if status_after == CampaignStatus.FAILED else None
            except Exception as exc:  # pragma: no cover - defensive
                campaign.status = CampaignStatus.FAILED
                campaign.error_code = "campaign_runner_failed"
                campaign.error_message = str(exc)
                campaign.request_id = run_id
                await db.flush()
                _log_failure_metric(
                    campaign_id=campaign.id,
                    company_id=campaign.company_id,
                    error_code="campaign_runner_failed",
                )
                status_after = CampaignStatus.FAILED
                reason = "campaign_runner_failed"

        logger.info(
            "campaign_claimed",
            extra={
                "campaign_id": campaign.id,
                "company_id": campaign.company_id,
                "attempt": 1,
                "status_before": status_before.value,
                "status_after": status_after.value,
            },
        )
        details.append(
            {
                "campaign_id": campaign.id,
                "company_id": campaign.company_id,
                "status_before": status_before.value,
                "status_after": status_after.value,
                "reason": reason,
            }
        )

    await db.commit()
    started = len(campaigns)
    skipped = max(0, found - started)
    return {
        "found": found,
        "started": started,
        "skipped": skipped,
        "details": details,
        "request_id": run_id,
    }


# Deprecated: use enqueue_due_campaigns + campaign_processing.
async def run_due_campaigns(
    db: AsyncSession,
    *,
    company_id: int,
    limit: int = 20,
    now: datetime | None = None,
    request_id: str | None = None,
) -> int:
    run_id = request_id or str(uuid4())
    now = now or _now_utc()
    limit_value = max(1, int(limit))

    where = [
        _due_campaigns_filter_with_retry(now=now),
        Campaign.deleted_at.is_(None),
        Campaign.company_id == int(company_id),
        or_(Campaign.scheduled_at.is_(None), Campaign.scheduled_at <= now),
    ]

    claim_stmt = (
        select(Campaign)
        .where(*where)
        .order_by(Campaign.scheduled_at.asc().nullsfirst(), Campaign.id.asc())
        .limit(limit_value)
        .with_for_update(skip_locked=True)
    )
    campaigns = (await db.execute(claim_stmt)).scalars().all()
    processed = 0

    for campaign in campaigns:
        campaign.status = CampaignStatus.RUNNING
        campaign.error_code = None
        campaign.error_message = None
        campaign.request_id = run_id
        await db.flush()

        await _record_campaign_event(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            status="started",
            request_id=run_id,
        )

        try:
            campaign.status = CampaignStatus.SUCCESS
            await _record_campaign_event(
                db,
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                status="success",
                request_id=run_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            error_msg = _truncate_error(str(exc))
            campaign.status = CampaignStatus.FAILED
            campaign.error_code = "campaign_run_failed"
            campaign.error_message = error_msg
            await _record_campaign_event(
                db,
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                status="failed",
                request_id=run_id,
                error_code="campaign_run_failed",
                error_message=error_msg,
            )
        processed += 1

    if processed:
        await db.commit()
    else:
        await db.rollback()
    return processed


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


# Deprecated: use enqueue_due_campaigns + campaign_processing.
def run_campaigns_sync(*, company_id: int | None = None, request_id: str | None = None) -> list[dict[str, Any]]:
    async def _runner():
        async with async_session_maker() as db:
            return await run_campaigns(db, company_id=company_id, request_id=request_id)

    try:
        return asyncio.run(_runner())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_runner())


# Deprecated: use enqueue_due_campaigns + campaign_processing.
async def process_scheduled_campaigns(
    *, company_id: int | None = None, request_id: str | None = None
) -> list[dict[str, Any]]:
    async with async_session_maker() as db:
        return await run_campaigns(db, company_id=company_id, request_id=request_id)


__all__ = [
    "enqueue_due_campaigns",
    "enqueue_due_campaigns_sync",
    "run_campaigns",
    "run_campaigns_with_claim",
    "run_due_campaigns",
    "process_scheduled_campaigns",
    "run_campaigns_sync",
]
