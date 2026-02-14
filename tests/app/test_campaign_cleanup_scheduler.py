from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus, ChannelType, Message
from app.models.company import Company
from app.worker import scheduler_worker

pytestmark = pytest.mark.asyncio


def _seed_campaign(
    *,
    company_id: int,
    processing_status: CampaignProcessingStatus,
    finished_at: datetime | None,
    failed_at: datetime | None,
    title_suffix: str,
) -> int:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        company = s.query(Company).filter(Company.id == company_id).first()
        if not company:
            company = Company(id=company_id, name=f"Company {company_id}")
            s.add(company)
            s.flush()

        campaign = Campaign(
            title=f"Cleanup sched {title_suffix}",
            description="cleanup",
            status=CampaignStatus.READY,
            scheduled_at=None,
            company_id=company.id,
            processing_status=processing_status,
            finished_at=finished_at,
            failed_at=failed_at,
        )
        s.add(campaign)
        s.flush()

        campaign.add_message(
            recipient="cleanup@example.com",
            content="Cleanup message",
            channel=ChannelType.EMAIL,
        )

        s.commit()
        s.refresh(campaign)
        return campaign.id


async def test_cleanup_job_skips_when_scheduler_disabled(monkeypatch, test_db):
    _ = test_db
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")

    now = datetime.now(UTC)
    done_old = _seed_campaign(
        company_id=9501,
        processing_status=CampaignProcessingStatus.DONE,
        finished_at=now - timedelta(days=20),
        failed_at=None,
        title_suffix="done-old",
    )

    await scheduler_worker.run_campaign_cleanup_job_async()

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        row = s.query(Campaign).filter(Campaign.id == done_old).first()
        assert row is not None
        assert row.deleted_at is None


async def test_cleanup_job_deletes_old_campaigns(monkeypatch, test_db):
    _ = test_db
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")

    now = datetime.now(UTC)
    done_old = _seed_campaign(
        company_id=9502,
        processing_status=CampaignProcessingStatus.DONE,
        finished_at=now - timedelta(days=20),
        failed_at=None,
        title_suffix="done-old",
    )
    done_recent = _seed_campaign(
        company_id=9502,
        processing_status=CampaignProcessingStatus.DONE,
        finished_at=now - timedelta(days=2),
        failed_at=None,
        title_suffix="done-recent",
    )

    await scheduler_worker.run_campaign_cleanup_job_async()

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        done_old_row = s.query(Campaign).filter(Campaign.id == done_old).first()
        done_recent_row = s.query(Campaign).filter(Campaign.id == done_recent).first()

        assert done_old_row is not None and done_old_row.deleted_at is not None
        assert done_recent_row is not None and done_recent_row.deleted_at is None

        old_messages = s.query(Message).filter(Message.campaign_id == done_old).all()
        assert old_messages
        assert all(msg.deleted_at is not None for msg in old_messages)
