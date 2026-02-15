from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus
from app.models.company import Company
from app.services import campaign_events
from app.services.campaign_pipeline import campaign_pipeline_tick
from app.worker import campaign_processing

pytestmark = pytest.mark.asyncio


async def _seed_campaign(
    async_db_session,
    *,
    company_id: int,
    processing_status: CampaignProcessingStatus,
) -> Campaign:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    campaign = Campaign(
        title=f"Event {company_id}-{processing_status.value}",
        description="test",
        status=CampaignStatus.DRAFT,
        scheduled_at=None,
        company_id=company_id,
        processing_status=processing_status,
        queued_at=datetime.now(UTC) if processing_status == CampaignProcessingStatus.QUEUED else None,
    )
    async_db_session.add(campaign)
    await async_db_session.commit()
    await async_db_session.refresh(campaign)
    return campaign


async def test_campaign_worker_failed_event_has_meta(async_db_session, monkeypatch):
    events: list[dict[str, object]] = []

    def _capture_event(**kwargs):
        events.append(kwargs)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(campaign_events.audit_logger, "log_system_event", _capture_event)
    monkeypatch.setattr(campaign_processing, "_perform_campaign_action", _boom)

    await _seed_campaign(
        async_db_session,
        company_id=94010,
        processing_status=CampaignProcessingStatus.QUEUED,
    )

    await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)

    failed_events = [e for e in events if e.get("event") == "campaign_worker_failed"]
    assert failed_events
    meta = failed_events[-1].get("meta") or {}
    for key in ("request_id", "company_id", "campaign_id", "status_before", "status_after", "attempt"):
        assert key in meta


async def test_campaign_pipeline_tick_event_has_counters(async_db_session, monkeypatch):
    events: list[dict[str, object]] = []

    def _capture_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(campaign_events.audit_logger, "log_system_event", _capture_event)

    await campaign_pipeline_tick(async_db_session, limit=5)

    tick_events = [e for e in events if e.get("event") == "campaign_pipeline_tick"]
    assert tick_events
    meta = tick_events[-1].get("meta") or {}
    for key in ("request_id", "queued", "processed", "failed"):
        assert key in meta
