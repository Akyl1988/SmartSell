from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.provider_registry import ProviderRegistry
from app.models.campaign import Campaign, CampaignStatus, ChannelType, Message, MessageStatus
from app.models.company import Company
from app.services.campaign_runner import run_campaigns

pytestmark = pytest.mark.asyncio


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


async def test_campaign_transitions_ready_to_success(async_db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    ProviderRegistry.invalidate()

    campaign = await _seed_campaign(async_db_session, company_id=1001, status=CampaignStatus.READY)
    results = await run_campaigns(async_db_session, company_id=1001, request_id="req-1")

    assert results
    await async_db_session.refresh(campaign)
    assert campaign.status == CampaignStatus.SUCCESS
    assert campaign.request_id == "req-1"


async def test_campaign_missing_provider_in_prod_fails(async_db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)

    async def _no_provider(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", _no_provider)

    campaign = await _seed_campaign(async_db_session, company_id=1002, status=CampaignStatus.READY)
    await run_campaigns(async_db_session, company_id=1002, request_id="req-2")

    await async_db_session.refresh(campaign)
    assert campaign.status == CampaignStatus.FAILED
    assert campaign.error_code == "messaging_provider_not_configured"


async def test_campaign_tenant_isolation(async_db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    ProviderRegistry.invalidate()

    campaign_a = await _seed_campaign(async_db_session, company_id=2001, status=CampaignStatus.READY)
    campaign_b = await _seed_campaign(async_db_session, company_id=2002, status=CampaignStatus.READY)

    await run_campaigns(async_db_session, company_id=2001, request_id="req-3")

    await async_db_session.refresh(campaign_a)
    await async_db_session.refresh(campaign_b)

    assert campaign_a.status == CampaignStatus.SUCCESS
    assert campaign_b.status == CampaignStatus.READY


def test_scheduler_worker_calls_runner(monkeypatch):
    called = {"ok": False}

    def _fake_runner(*_args, **_kwargs):
        called["ok"] = True
        return []

    monkeypatch.setattr("app.worker.scheduler_worker.run_campaigns_sync", _fake_runner)

    from app.worker import scheduler_worker

    scheduler_worker.process_scheduled_campaigns()
    assert called["ok"] is True
