from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus, ChannelType, Message, MessageStatus
from app.models.company import Company
from app.services.campaign_runner import enqueue_due_campaigns
from app.worker import campaign_processing


async def _seed_campaign(async_db_session, *, company_id: int, status: CampaignStatus, scheduled_at=None):
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    campaign = Campaign(
        title=f"Camp {company_id}-{status.value}",
        description="test",
        status=status,
        scheduled_at=scheduled_at,
        company_id=company_id,
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


async def test_enqueue_due_campaigns_queues_ready(async_db_session):
    campaign = await _seed_campaign(async_db_session, company_id=1001, status=CampaignStatus.READY)

    summary = await enqueue_due_campaigns(async_db_session, company_id=1001, request_id="req-1")

    assert summary["queued"] == 1
    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED
    assert campaign.queued_at is not None


async def test_enqueue_due_campaigns_idempotent(async_db_session):
    campaign = await _seed_campaign(async_db_session, company_id=2001, status=CampaignStatus.READY)

    first = await enqueue_due_campaigns(async_db_session, company_id=2001, request_id="req-2")
    second = await enqueue_due_campaigns(async_db_session, company_id=2001, request_id="req-3")

    assert first["queued"] == 1
    assert second["queued"] == 0
    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED


async def test_enqueue_due_campaigns_tenant_scoped(async_db_session):
    campaign_a = await _seed_campaign(async_db_session, company_id=3003, status=CampaignStatus.READY)
    campaign_b = await _seed_campaign(async_db_session, company_id=3004, status=CampaignStatus.READY)

    summary = await enqueue_due_campaigns(async_db_session, company_id=3003, request_id="req-4")

    assert summary["queued"] == 1
    await async_db_session.refresh(campaign_a)
    await async_db_session.refresh(campaign_b)
    assert campaign_a.processing_status == CampaignProcessingStatus.QUEUED
    assert campaign_b.processing_status != CampaignProcessingStatus.QUEUED


def test_scheduler_worker_calls_runner(monkeypatch):
    called = {"enqueue": False, "process": False, "schedule": False, "scheduled_ids": []}

    def _fake_enqueue(*_args, **_kwargs):
        called["enqueue"] = True
        return {"queued": 0, "skipped": 0, "campaign_ids": []}

    def _fake_process(*_args, **_kwargs):
        called["process"] = True
        return [{"campaign_id": 10, "status": CampaignProcessingStatus.DONE.value}]

    def _fake_schedule(campaign_ids):
        called["schedule"] = True
        called["scheduled_ids"] = list(campaign_ids)
        return 1

    monkeypatch.setattr("app.worker.scheduler_worker.enqueue_due_campaigns_sync", _fake_enqueue)
    monkeypatch.setattr("app.worker.campaign_processing.process_campaign_queue_once_sync", _fake_process)
    monkeypatch.setattr("app.worker.scheduler_worker._schedule_pending_messages_for_campaigns", _fake_schedule)

    from app.worker import scheduler_worker

    scheduler_worker.process_scheduled_campaigns()
    assert called["enqueue"] is True
    assert called["process"] is True
    assert called["schedule"] is True
    assert called["scheduled_ids"] == [10]


async def test_enqueue_then_process_campaign(async_db_session, monkeypatch):
    campaign = await _seed_campaign(async_db_session, company_id=4001, status=CampaignStatus.READY)

    summary = await enqueue_due_campaigns(async_db_session, company_id=4001, request_id="req-4", limit=10)
    assert summary["queued"] == 1

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED
    assert campaign.queued_at is not None

    results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
    assert results

    await async_db_session.refresh(campaign)
    assert campaign.processing_status in {CampaignProcessingStatus.DONE, CampaignProcessingStatus.FAILED}
