from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignProcessingStatus, Message

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _select_campaign_ids(
    db: AsyncSession,
    *,
    status: CampaignProcessingStatus,
    cutoff: datetime,
    limit: int,
    ts_col,
) -> list[int]:
    if limit <= 0:
        return []
    ts_expr = ts_col
    stmt = (
        sa.select(Campaign.id)
        .where(
            Campaign.processing_status == status,
            Campaign.deleted_at.is_(None),
            ts_expr.is_not(None),
            ts_expr < cutoff,
        )
        .order_by(ts_expr.asc(), Campaign.id.asc())
        .limit(limit)
    )
    rows = await db.execute(stmt)
    return [int(cid) for cid in rows.scalars().all()]


async def campaign_cleanup_run(
    db: AsyncSession,
    *,
    done_days: int,
    failed_days: int,
    limit: int,
    now: datetime | None = None,
) -> dict:
    cleanup_now = now or _utcnow()
    done_cutoff = cleanup_now - timedelta(days=done_days)
    failed_cutoff = cleanup_now - timedelta(days=failed_days)

    remaining = int(limit)
    done_ids = await _select_campaign_ids(
        db,
        status=CampaignProcessingStatus.DONE,
        cutoff=done_cutoff,
        limit=remaining,
        ts_col=Campaign.finished_at,
    )
    remaining -= len(done_ids)
    failed_ids: list[int] = []
    if remaining > 0:
        failed_ids = await _select_campaign_ids(
            db,
            status=CampaignProcessingStatus.FAILED,
            cutoff=failed_cutoff,
            limit=remaining,
            ts_col=Campaign.failed_at,
        )

    campaign_ids = done_ids + failed_ids
    deleted_messages = 0
    deleted_campaigns = 0

    if campaign_ids:
        msg_stmt = (
            sa.update(Message)
            .where(Message.campaign_id.in_(campaign_ids), Message.deleted_at.is_(None))
            .values(deleted_at=cleanup_now, delete_reason="campaign_cleanup")
        )
        msg_result = await db.execute(msg_stmt)
        deleted_messages = int(msg_result.rowcount or 0)

        camp_stmt = (
            sa.update(Campaign)
            .where(Campaign.id.in_(campaign_ids), Campaign.deleted_at.is_(None))
            .values(deleted_at=cleanup_now, delete_reason="campaign_cleanup")
        )
        camp_result = await db.execute(camp_stmt)
        deleted_campaigns = int(camp_result.rowcount or 0)

    counters = {
        "scanned_done": len(done_ids),
        "scanned_failed": len(failed_ids),
        "deleted_campaigns": deleted_campaigns,
        "deleted_messages": deleted_messages,
    }
    logger.info(
        "campaign_cleanup_run: scanned_done=%s scanned_failed=%s deleted_campaigns=%s deleted_messages=%s",
        counters["scanned_done"],
        counters["scanned_failed"],
        counters["deleted_campaigns"],
        counters["deleted_messages"],
    )
    return counters
