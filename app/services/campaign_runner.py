from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_maker
from app.core.logging import bound_context, get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.models.campaign import Campaign, CampaignStatus, Message, MessageStatus
from app.services.messaging_providers import MessagingProviderResolver
from app.services.retry_policy import RetryPolicy

logger = get_logger(__name__)

_DEFAULT_RETRY = RetryPolicy(timeout_seconds=8.0, retries=2, backoff_seconds=0.5)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _log_failure_metric(*, campaign_id: int, company_id: int, error_code: str) -> None:
    logger.warning(
        "metric=campaign_job_failures", extra={"campaign_id": campaign_id, "company_id": company_id, "code": error_code}
    )


def _due_campaigns_filter(*, now: datetime) -> Any:
    return or_(
        Campaign.status == CampaignStatus.READY,
        and_(Campaign.status == CampaignStatus.SCHEDULED, Campaign.scheduled_at <= now),
    )


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


def run_campaigns_sync(*, company_id: int | None = None, request_id: str | None = None) -> list[dict[str, Any]]:
    async def _runner():
        async with async_session_maker() as db:
            return await run_campaigns(db, company_id=company_id, request_id=request_id)

    try:
        return asyncio.run(_runner())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_runner())


async def process_scheduled_campaigns(
    *, company_id: int | None = None, request_id: str | None = None
) -> list[dict[str, Any]]:
    async with async_session_maker() as db:
        return await run_campaigns(db, company_id=company_id, request_id=request_id)


__all__ = ["run_campaigns", "run_campaigns_with_claim", "process_scheduled_campaigns", "run_campaigns_sync"]
