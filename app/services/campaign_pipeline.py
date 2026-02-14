from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import CampaignProcessingStatus, Message, MessageStatus
from app.services.campaign_runner import enqueue_due_campaigns
from app.worker import campaign_processing
from app.worker.scheduler_worker import _schedule_message_send

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def campaign_pipeline_tick(
    db: AsyncSession,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> dict:
    tick_now = now or _utcnow()
    enqueue_summary = await enqueue_due_campaigns(
        db,
        request_id=str(uuid4()),
        now=tick_now,
        limit=limit,
    )
    processed = await campaign_processing.process_campaign_queue_once(db, limit=limit, now=tick_now)

    processed_ids = [item.get("campaign_id") for item in processed if item.get("campaign_id")]
    processed_done_ids = [
        item.get("campaign_id")
        for item in processed
        if item.get("status") == CampaignProcessingStatus.DONE.value and item.get("campaign_id")
    ]
    failed_count = sum(1 for item in processed if item.get("status") == CampaignProcessingStatus.FAILED.value)

    scheduled = 0
    scheduled_message_ids: list[int] = []
    if processed_done_ids:
        rows = (
            await db.execute(
                select(Message.id).where(
                    Message.campaign_id.in_(processed_done_ids),
                    Message.status == MessageStatus.PENDING,
                )
            )
        ).all()
        for (message_id,) in rows:
            try:
                if _schedule_message_send(int(message_id)):
                    scheduled += 1
                    scheduled_message_ids.append(int(message_id))
            except Exception as exc:  # pragma: no cover - diagnostics only
                logger.warning("campaign_message_schedule_failed: %s", exc)

    counters = {
        "queued": int(enqueue_summary.get("queued", 0)),
        "skipped": int(enqueue_summary.get("skipped", 0)),
        "processed": len(processed),
        "scheduled": scheduled,
        "failed": failed_count,
        "campaign_ids": enqueue_summary.get("campaign_ids", []),
        "processed_ids": processed_ids,
        "scheduled_message_ids": scheduled_message_ids,
    }

    logger.info(
        "campaign_pipeline_tick: queued=%s skipped=%s processed=%s scheduled=%s failed=%s",
        counters["queued"],
        counters["skipped"],
        counters["processed"],
        counters["scheduled"],
        counters["failed"],
    )
    return counters
