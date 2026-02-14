from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.core.security import create_access_token, get_password_hash
from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus, ChannelType, Message
from app.models.company import Company
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _platform_admin_headers_without_company() -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990012").first()
        if not user:
            user = User(
                phone="+79999990012",
                company_id=None,
                hashed_password=get_password_hash("Secret123!"),
                role="platform_admin",
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = None
            user.role = "platform_admin"
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id, extra={"role": "platform_admin"})
    return {"Authorization": f"Bearer {token}"}


def _superuser_headers_without_company(*, is_superuser: bool = True) -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990013").first()
        if not user:
            user = User(
                phone="+79999990013",
                company_id=None,
                hashed_password=get_password_hash("Secret123!"),
                role="admin",
                is_superuser=is_superuser,
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = None
            user.role = "admin"
            user.is_superuser = is_superuser
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


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
            title=f"Cleanup campaign {title_suffix}",
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


async def test_campaign_cleanup_requires_auth(async_client):
    resp = await async_client.post("/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=10")
    assert resp.status_code == 401, resp.text
    payload = resp.json()
    assert payload.get("code") == "AUTH_REQUIRED"


async def test_campaign_cleanup_denies_store_admin(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=10",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"


async def test_campaign_cleanup_allows_platform_admin(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()

    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=10",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_campaign_cleanup_allows_superuser(async_client, test_db, monkeypatch):
    _ = test_db
    from app.core.config import settings

    monkeypatch.setattr(settings, "SUPERUSER_ALLOWLIST", ["+79999990013"])
    headers = _superuser_headers_without_company(is_superuser=False)

    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=10",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_campaign_cleanup_deletes_old_campaigns(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()
    now = datetime.now(UTC)

    done_old = _seed_campaign(
        company_id=9401,
        processing_status=CampaignProcessingStatus.DONE,
        finished_at=now - timedelta(days=20),
        failed_at=None,
        title_suffix="done-old",
    )
    done_recent = _seed_campaign(
        company_id=9401,
        processing_status=CampaignProcessingStatus.DONE,
        finished_at=now - timedelta(days=2),
        failed_at=None,
        title_suffix="done-recent",
    )
    failed_old = _seed_campaign(
        company_id=9401,
        processing_status=CampaignProcessingStatus.FAILED,
        finished_at=None,
        failed_at=now - timedelta(days=40),
        title_suffix="failed-old",
    )
    failed_recent = _seed_campaign(
        company_id=9401,
        processing_status=CampaignProcessingStatus.FAILED,
        finished_at=None,
        failed_at=now - timedelta(days=5),
        title_suffix="failed-recent",
    )

    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=10",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("deleted_campaigns", 0) >= 2
    assert payload.get("deleted_messages", 0) >= 2

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        done_old_row = s.query(Campaign).filter(Campaign.id == done_old).first()
        done_recent_row = s.query(Campaign).filter(Campaign.id == done_recent).first()
        failed_old_row = s.query(Campaign).filter(Campaign.id == failed_old).first()
        failed_recent_row = s.query(Campaign).filter(Campaign.id == failed_recent).first()

        assert done_old_row is not None and done_old_row.deleted_at is not None
        assert failed_old_row is not None and failed_old_row.deleted_at is not None
        assert done_recent_row is not None and done_recent_row.deleted_at is None
        assert failed_recent_row is not None and failed_recent_row.deleted_at is None

        old_messages = s.query(Message).filter(Message.campaign_id.in_([done_old, failed_old])).all()
        assert old_messages
        assert all(msg.deleted_at is not None for msg in old_messages)

        recent_messages = s.query(Message).filter(Message.campaign_id.in_([done_recent, failed_recent])).all()
        assert recent_messages
        assert all(msg.deleted_at is None for msg in recent_messages)
