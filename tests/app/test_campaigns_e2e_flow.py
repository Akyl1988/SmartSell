from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.campaign import (
    Campaign,
    CampaignProcessingStatus,
    CampaignStatus,
    ChannelType,
    Message,
    MessageStatus,
)
from app.models.company import Company
from app.services import campaign_events
from app.services.campaign_runner import enqueue_due_campaigns
from app.worker.campaign_processing import process_campaign_queue_once

pytestmark = pytest.mark.asyncio


def _assert_event_meta(meta: dict[str, object], *, request_id: str, require_duration: bool) -> None:
    for key in ("request_id", "company_id", "campaign_id", "attempt"):
        assert key in meta
    assert meta.get("request_id") == request_id
    if require_duration:
        assert "duration_ms" in meta


async def _seed_due_campaign(async_db_session, *, company_id: int, now: datetime, request_id: str) -> Campaign:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    campaign = Campaign(
        title=f"E2E {company_id}-{now.isoformat()}",
        description="test",
        status=CampaignStatus.READY,
        scheduled_at=None,
        company_id=company_id,
        processing_status=CampaignProcessingStatus.DONE,
        next_attempt_at=now - timedelta(minutes=1),
        request_id=request_id,
    )
    async_db_session.add(campaign)
    await async_db_session.flush()

    message = Message(
        campaign_id=campaign.id,
        recipient="user@example.com",
        content="Hello",
        status=MessageStatus.PENDING,
        channel=ChannelType.EMAIL,
    )
    async_db_session.add(message)

    await async_db_session.commit()
    await async_db_session.refresh(campaign)
    return campaign


async def test_campaigns_e2e_flow(async_db_session, monkeypatch):
    events: list[dict[str, object]] = []

    def _capture_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(campaign_events.audit_logger, "log_system_event", _capture_event)

    now = datetime.now(UTC)
    campaign = await _seed_due_campaign(async_db_session, company_id=77701, now=now, request_id="e2e")

    summary = await enqueue_due_campaigns(
        async_db_session,
        company_id=campaign.company_id,
        request_id="e2e",
        now=now,
        limit=10,
    )
    assert summary["queued"] == 1

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED
    assert campaign.queued_at is not None

    results = await process_campaign_queue_once(async_db_session, now=now, limit=10)
    assert results

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.DONE
    assert campaign.started_at is not None
    assert campaign.finished_at is not None

    def _last_event(name: str) -> dict[str, object]:
        matches = [event for event in events if event.get("event") == name]
        assert matches
        return matches[-1]

    enqueue_due = _last_event("campaign_enqueue_due")
    _assert_event_meta(enqueue_due.get("meta") or {}, request_id="e2e", require_duration=True)

    enqueued = _last_event("campaign_enqueued")
    _assert_event_meta(enqueued.get("meta") or {}, request_id="e2e", require_duration=False)

    worker_start = _last_event("campaign_worker_start")
    _assert_event_meta(worker_start.get("meta") or {}, request_id="e2e", require_duration=False)

    worker_success = _last_event("campaign_worker_success")
    _assert_event_meta(worker_success.get("meta") or {}, request_id="e2e", require_duration=True)
