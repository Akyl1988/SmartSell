from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.campaign import Campaign, CampaignProcessingStatus, Message

_ERROR_MESSAGE_LIMIT = 500
_NO_MESSAGES_ERROR = "campaign_has_no_messages"
_QUEUE_LOCK_KEY = 0x43505051  # "CPPQ"
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _truncate_error(message: str | None, limit: int = _ERROR_MESSAGE_LIMIT) -> str | None:
    if not message:
        return None
    cleaned = message.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _campaign_lock_key(campaign_id: int) -> int:
    namespace = 0x434D50  # "CMP"
    return (namespace << 32) ^ int(campaign_id)


async def _try_queue_advisory_lock(db: AsyncSession) -> bool:
    res = await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)").bindparams(k=_QUEUE_LOCK_KEY))
    return bool(res.scalar())


def _try_queue_advisory_lock_sync(db: Session) -> bool:
    res = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _QUEUE_LOCK_KEY})
    return bool(res.scalar())


async def _try_campaign_advisory_lock(db: AsyncSession, campaign_id: int) -> bool:
    res = await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)").bindparams(k=_campaign_lock_key(campaign_id)))
    return bool(res.scalar())


def _try_campaign_advisory_lock_sync(db: Session, campaign_id: int) -> bool:
    res = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _campaign_lock_key(campaign_id)})
    return bool(res.scalar())


async def _perform_campaign_action(db: AsyncSession, campaign: Campaign) -> None:
    existing = await db.execute(select(Message.id).where(Message.campaign_id == campaign.id).limit(1))
    if existing.scalar() is not None:
        return

    raise RuntimeError(_NO_MESSAGES_ERROR)


def _perform_campaign_action_sync(db: Session, campaign: Campaign) -> None:
    existing = db.execute(select(Message.id).where(Message.campaign_id == campaign.id).limit(1)).scalar()
    if existing is not None:
        return

    raise RuntimeError(_NO_MESSAGES_ERROR)


async def process_campaign_queue_once(
    db: AsyncSession,
    *,
    limit: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _utcnow()
    if not await _try_queue_advisory_lock(db):
        return []
    batch_limit = min(int(settings.CAMPAIGN_PROCESS_BATCH), max(1, int(limit)))
    claim_stmt = (
        select(Campaign.id)
        .where(
            Campaign.processing_status == CampaignProcessingStatus.QUEUED,
            Campaign.deleted_at.is_(None),
        )
        .order_by(Campaign.queued_at.asc().nullsfirst(), Campaign.id.asc())
        .limit(batch_limit)
        .with_for_update(skip_locked=True)
    )
    campaign_ids = [row[0] for row in (await db.execute(claim_stmt)).all()]
    await db.rollback()

    results: list[dict[str, Any]] = []
    for campaign_id in campaign_ids:
        async with db.begin():
            got_lock = await _try_campaign_advisory_lock(db, campaign_id)
            if not got_lock:
                continue

            campaign = (
                await db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
            ).scalar_one()
            if campaign.processing_status != CampaignProcessingStatus.QUEUED:
                continue

            if not campaign.request_id:
                campaign.request_id = str(uuid4())
            request_id = campaign.request_id

            attempts = int(campaign.attempts or 0)
            max_attempts = int(settings.CAMPAIGN_MAX_ATTEMPTS)
            if max_attempts > 0 and attempts >= max_attempts:
                campaign.processing_status = CampaignProcessingStatus.FAILED
                campaign.last_error = "max_attempts_exceeded"
                campaign.finished_at = _utcnow()
                campaign.failed_at = campaign.finished_at
                logger.warning(
                    "campaign_processing_max_attempts",
                    extra={"campaign_id": campaign.id, "request_id": request_id},
                )
                results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})
                continue

            campaign.processing_status = CampaignProcessingStatus.PROCESSING
            campaign.started_at = now
            campaign.finished_at = None
            campaign.failed_at = None
            campaign.last_error = None
            await db.flush()

            try:
                await _perform_campaign_action(db, campaign)
                campaign.processing_status = CampaignProcessingStatus.DONE
                campaign.finished_at = _utcnow()
                campaign.failed_at = None
                results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})
            except Exception as exc:
                error_message = _truncate_error(str(exc))
                attempts = int(campaign.attempts or 0) + 1
                campaign.attempts = attempts
                campaign.finished_at = _utcnow()
                campaign.failed_at = campaign.finished_at
                campaign.last_error = error_message
                logger.error(
                    "campaign_processing_failed",
                    extra={
                        "campaign_id": campaign.id,
                        "request_id": request_id,
                        "attempts": attempts,
                        "error": error_message,
                    },
                )
                if max_attempts > 0 and attempts >= max_attempts:
                    campaign.processing_status = CampaignProcessingStatus.FAILED
                    campaign.last_error = "max_attempts_exceeded"
                else:
                    campaign.processing_status = CampaignProcessingStatus.QUEUED
                    campaign.queued_at = _utcnow()
                results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})

    return results


def process_campaign_queue_once_sync(
    *,
    limit: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _utcnow()
    with session_scope() as db:
        if not _try_queue_advisory_lock_sync(db):
            return []
        batch_limit = min(int(settings.CAMPAIGN_PROCESS_BATCH), max(1, int(limit)))
        claim_stmt = (
            select(Campaign.id)
            .where(
                Campaign.processing_status == CampaignProcessingStatus.QUEUED,
                Campaign.deleted_at.is_(None),
            )
            .order_by(Campaign.queued_at.asc().nullsfirst(), Campaign.id.asc())
            .limit(batch_limit)
            .with_for_update(skip_locked=True)
        )
        campaign_ids = [row[0] for row in db.execute(claim_stmt).all()]
        db.rollback()

        results: list[dict[str, Any]] = []
        for campaign_id in campaign_ids:
            with db.begin():
                if not _try_campaign_advisory_lock_sync(db, campaign_id):
                    continue

                campaign = db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update()).scalar_one()
                if campaign.processing_status != CampaignProcessingStatus.QUEUED:
                    continue

                if not campaign.request_id:
                    campaign.request_id = str(uuid4())
                request_id = campaign.request_id

                attempts = int(campaign.attempts or 0)
                max_attempts = int(settings.CAMPAIGN_MAX_ATTEMPTS)
                if max_attempts > 0 and attempts >= max_attempts:
                    campaign.processing_status = CampaignProcessingStatus.FAILED
                    campaign.last_error = "max_attempts_exceeded"
                    campaign.finished_at = _utcnow()
                    campaign.failed_at = campaign.finished_at
                    logger.warning(
                        "campaign_processing_max_attempts",
                        extra={"campaign_id": campaign.id, "request_id": request_id},
                    )
                    results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})
                    continue

                campaign.processing_status = CampaignProcessingStatus.PROCESSING
                campaign.started_at = now
                campaign.finished_at = None
                campaign.failed_at = None
                campaign.last_error = None
                db.flush()

                try:
                    _perform_campaign_action_sync(db, campaign)
                    campaign.processing_status = CampaignProcessingStatus.DONE
                    campaign.finished_at = _utcnow()
                    campaign.failed_at = None
                    results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})
                except Exception as exc:
                    error_message = _truncate_error(str(exc))
                    attempts = int(campaign.attempts or 0) + 1
                    campaign.attempts = attempts
                    campaign.finished_at = _utcnow()
                    campaign.failed_at = campaign.finished_at
                    campaign.last_error = error_message
                    logger.error(
                        "campaign_processing_failed",
                        extra={
                            "campaign_id": campaign.id,
                            "request_id": request_id,
                            "attempts": attempts,
                            "error": error_message,
                        },
                    )
                    if max_attempts > 0 and attempts >= max_attempts:
                        campaign.processing_status = CampaignProcessingStatus.FAILED
                        campaign.last_error = "max_attempts_exceeded"
                    else:
                        campaign.processing_status = CampaignProcessingStatus.QUEUED
                        campaign.queued_at = _utcnow()
                    results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})

        return results


__all__ = ["process_campaign_queue_once", "process_campaign_queue_once_sync"]
