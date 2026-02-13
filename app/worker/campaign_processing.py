from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.campaign import Campaign, CampaignProcessingStatus, ChannelType, Message, MessageStatus

_ERROR_MESSAGE_LIMIT = 500
_PLACEHOLDER_BLOCKED = "campaign_placeholder_not_allowed_in_production"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_prod() -> bool:
    return str(getattr(settings, "ENVIRONMENT", "")).lower() == "production"


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

    if _is_prod():
        raise RuntimeError(_PLACEHOLDER_BLOCKED)

    message = Message(
        campaign_id=campaign.id,
        recipient="placeholder@example.com",
        content=f"Campaign {campaign.id} placeholder",
        status=MessageStatus.PENDING,
        channel=ChannelType.EMAIL,
    )
    db.add(message)


def _perform_campaign_action_sync(db: Session, campaign: Campaign) -> None:
    existing = db.execute(select(Message.id).where(Message.campaign_id == campaign.id).limit(1)).scalar()
    if existing is not None:
        return

    if _is_prod():
        raise RuntimeError(_PLACEHOLDER_BLOCKED)

    message = Message(
        campaign_id=campaign.id,
        recipient="placeholder@example.com",
        content=f"Campaign {campaign.id} placeholder",
        status=MessageStatus.PENDING,
        channel=ChannelType.EMAIL,
    )
    db.add(message)


async def process_campaign_queue_once(
    db: AsyncSession,
    *,
    limit: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _utcnow()
    claim_stmt = (
        select(Campaign.id)
        .where(
            Campaign.processing_status == CampaignProcessingStatus.QUEUED,
            Campaign.deleted_at.is_(None),
        )
        .order_by(Campaign.queued_at.asc().nullsfirst(), Campaign.id.asc())
        .limit(max(1, int(limit)))
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

            campaign.processing_status = CampaignProcessingStatus.PROCESSING
            campaign.started_at = now
            campaign.finished_at = None
            campaign.last_error = None
            await db.flush()

            try:
                await _perform_campaign_action(db, campaign)
                campaign.processing_status = CampaignProcessingStatus.DONE
                campaign.finished_at = _utcnow()
                results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})
            except Exception as exc:
                campaign.processing_status = CampaignProcessingStatus.FAILED
                campaign.last_error = _truncate_error(str(exc))
                campaign.attempts = int(campaign.attempts or 0) + 1
                campaign.finished_at = _utcnow()
                results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})

    return results


def process_campaign_queue_once_sync(
    *,
    limit: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _utcnow()
    with session_scope() as db:
        claim_stmt = (
            select(Campaign.id)
            .where(
                Campaign.processing_status == CampaignProcessingStatus.QUEUED,
                Campaign.deleted_at.is_(None),
            )
            .order_by(Campaign.queued_at.asc().nullsfirst(), Campaign.id.asc())
            .limit(max(1, int(limit)))
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

                campaign.processing_status = CampaignProcessingStatus.PROCESSING
                campaign.started_at = now
                campaign.finished_at = None
                campaign.last_error = None
                db.flush()

                try:
                    _perform_campaign_action_sync(db, campaign)
                    campaign.processing_status = CampaignProcessingStatus.DONE
                    campaign.finished_at = _utcnow()
                    results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})
                except Exception as exc:
                    campaign.processing_status = CampaignProcessingStatus.FAILED
                    campaign.last_error = _truncate_error(str(exc))
                    campaign.attempts = int(campaign.attempts or 0) + 1
                    campaign.finished_at = _utcnow()
                    results.append({"campaign_id": campaign.id, "status": campaign.processing_status.value})

        return results


__all__ = ["process_campaign_queue_once", "process_campaign_queue_once_sync"]
