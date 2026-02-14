from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus, ChannelType, Message, MessageStatus
from app.models.company import Company
from app.worker import campaign_processing

pytestmark = pytest.mark.asyncio


async def _seed_campaign(
    async_db_session,
    *,
    company_id: int,
    processing_status: CampaignProcessingStatus,
    add_message: bool = True,
    attempts: int = 0,
    request_id: str | None = None,
) -> Campaign:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    campaign = Campaign(
        title=f"Processing {company_id}-{processing_status.value}",
        description="test",
        status=CampaignStatus.DRAFT,
        scheduled_at=None,
        company_id=company_id,
        processing_status=processing_status,
        queued_at=datetime.now(UTC) if processing_status == CampaignProcessingStatus.QUEUED else None,
        attempts=attempts,
        request_id=request_id,
    )
    async_db_session.add(campaign)
    await async_db_session.flush()

    if add_message:
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


async def test_campaign_run_endpoint_idempotent(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91010,
        processing_status=CampaignProcessingStatus.DONE,
    )

    first = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/run",
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text
    payload_first = first.json()
    assert payload_first.get("status") == CampaignProcessingStatus.QUEUED.value
    queued_at = payload_first.get("queued_at")
    request_id = payload_first.get("request_id")
    assert queued_at
    assert request_id

    second = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/run",
        headers=auth_headers,
    )
    assert second.status_code == 200, second.text
    payload_second = second.json()
    assert payload_second.get("status") == CampaignProcessingStatus.QUEUED.value
    assert payload_second.get("queued_at") == queued_at
    assert payload_second.get("request_id") == request_id
    assert "failed_at" in payload_second


async def test_campaign_manual_run_resets_attempts(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91011,
        processing_status=CampaignProcessingStatus.QUEUED,
    )
    campaign.attempts = settings.CAMPAIGN_MAX_ATTEMPTS
    campaign.last_error = "max_attempts_exceeded"
    campaign.failed_at = datetime.now(UTC)
    campaign.finished_at = campaign.failed_at
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/run",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED
    assert campaign.attempts == 0
    assert campaign.last_error is None
    assert campaign.failed_at is None
    assert campaign.finished_at is None
    assert campaign.queued_at is not None


async def test_campaign_worker_transitions_to_done(async_db_session):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91020,
        processing_status=CampaignProcessingStatus.QUEUED,
    )

    results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
    assert results

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.DONE
    assert campaign.started_at is not None
    assert campaign.finished_at is not None
    assert campaign.failed_at is None


async def test_campaign_worker_failure_sets_error(async_db_session, monkeypatch):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91030,
        processing_status=CampaignProcessingStatus.QUEUED,
        request_id="req-keep",
    )

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(campaign_processing, "_perform_campaign_action", _boom)

    await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
    await async_db_session.refresh(campaign)

    max_attempts = int(settings.CAMPAIGN_MAX_ATTEMPTS)
    assert campaign.attempts == 1
    assert campaign.failed_at is not None
    assert campaign.request_id == "req-keep"
    if max_attempts > 0 and max_attempts > 1:
        assert campaign.processing_status == CampaignProcessingStatus.QUEUED
        assert campaign.last_error
        assert "boom" in campaign.last_error
        assert campaign.queued_at is not None
    else:
        assert campaign.processing_status == CampaignProcessingStatus.FAILED
        assert campaign.last_error == "max_attempts_exceeded"


async def test_campaign_worker_lock_prevents_processing(async_db_session, monkeypatch):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91040,
        processing_status=CampaignProcessingStatus.QUEUED,
    )

    async def _deny_lock(*_args, **_kwargs):
        return False

    monkeypatch.setattr(campaign_processing, "_try_campaign_advisory_lock", _deny_lock)

    results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
    assert results == []

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED


async def test_campaign_worker_queue_lock_prevents_processing(async_db_session, monkeypatch):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91041,
        processing_status=CampaignProcessingStatus.QUEUED,
    )

    async def _deny_queue_lock(*_args, **_kwargs):
        return False

    monkeypatch.setattr(campaign_processing, "_try_queue_advisory_lock", _deny_queue_lock)

    results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
    assert results == []

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.QUEUED


async def test_campaign_worker_batch_limit(async_db_session):
    campaigns = []
    batch_size = int(settings.CAMPAIGN_PROCESS_BATCH)
    for idx in range(batch_size + 10):
        campaigns.append(
            await _seed_campaign(
                async_db_session,
                company_id=92000 + idx,
                processing_status=CampaignProcessingStatus.QUEUED,
            )
        )

    results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=200)
    assert len(results) == batch_size


async def test_campaign_worker_max_attempts(async_db_session):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=93000,
        processing_status=CampaignProcessingStatus.QUEUED,
        attempts=settings.CAMPAIGN_MAX_ATTEMPTS,
    )

    results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
    assert results

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.FAILED
    assert campaign.failed_at is not None
    assert campaign.started_at is None
    assert campaign.attempts == settings.CAMPAIGN_MAX_ATTEMPTS
    assert campaign.last_error == "max_attempts_exceeded"


async def test_campaign_worker_requires_messages(async_db_session):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91050,
        processing_status=CampaignProcessingStatus.QUEUED,
        add_message=False,
    )

    max_attempts = int(settings.CAMPAIGN_MAX_ATTEMPTS)
    for _ in range(max(1, max_attempts)):
        results = await campaign_processing.process_campaign_queue_once(async_db_session, limit=5)
        assert results

    await async_db_session.refresh(campaign)
    assert campaign.attempts == max_attempts
    assert campaign.processing_status == CampaignProcessingStatus.FAILED
    assert campaign.last_error == "max_attempts_exceeded"
    assert campaign.failed_at is not None

    message = (
        await async_db_session.execute(select(Message.id).where(Message.campaign_id == campaign.id).limit(1))
    ).scalar_one_or_none()
    assert message is None
