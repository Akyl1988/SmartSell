from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.core.security import create_access_token, get_password_hash
from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.billing import Subscription
from app.models.campaign import (
    Campaign,
    CampaignProcessingStatus,
    CampaignStatus,
    ChannelType,
    Message,
    MessageStatus,
)
from app.models.company import Company
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _superuser_headers_without_company() -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990031").first()
        if not user:
            user = User(
                phone="+79999990031",
                company_id=None,
                hashed_password=get_password_hash("Secret123!"),
                role="admin",
                is_superuser=True,
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = None
            user.role = "admin"
            user.is_superuser = True
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_campaign(async_db_session, *, company_id: int, title_suffix: str) -> Campaign:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    existing_sub = (
        await async_db_session.execute(
            Subscription.__table__.select().where(
                Subscription.company_id == company_id,
                Subscription.deleted_at.is_(None),
            )
        )
    ).first()
    if not existing_sub:
        async_db_session.add(
            Subscription(
                company_id=company_id,
                plan=normalize_plan_id("start") or "trial",
                status="active",
                billing_cycle="monthly",
                price=0,
                currency="KZT",
            )
        )

    campaign = Campaign(
        title=f"Run campaign {title_suffix}",
        description="test",
        status=CampaignStatus.READY,
        scheduled_at=None,
        company_id=company_id,
        processing_status=CampaignProcessingStatus.DONE,
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


async def test_campaign_run_store_admin_idempotent(async_client, async_db_session, company_a_admin_headers):
    campaign = await _seed_campaign(async_db_session, company_id=1001, title_suffix="admin")

    first = await async_client.post(
        f"/api/v1/campaigns/{campaign.id}/run",
        headers=company_a_admin_headers,
    )
    assert first.status_code == 200, first.text
    payload_first = first.json()
    assert payload_first.get("status") == CampaignProcessingStatus.QUEUED.value
    queued_at = payload_first.get("queued_at")
    assert queued_at

    second = await async_client.post(
        f"/api/v1/campaigns/{campaign.id}/run",
        headers=company_a_admin_headers,
    )
    assert second.status_code == 200, second.text
    payload_second = second.json()
    assert payload_second.get("status") == CampaignProcessingStatus.QUEUED.value
    assert payload_second.get("queued_at") == queued_at


async def test_campaign_run_store_manager_allowed(async_client, async_db_session, company_a_manager_headers):
    campaign = await _seed_campaign(async_db_session, company_id=1001, title_suffix="manager")

    resp = await async_client.post(
        f"/api/v1/campaigns/{campaign.id}/run",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_campaign_run_store_employee_forbidden(async_client, async_db_session, company_a_employee_headers):
    campaign = await _seed_campaign(async_db_session, company_id=1001, title_suffix="employee")

    resp = await async_client.post(
        f"/api/v1/campaigns/{campaign.id}/run",
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text


async def test_campaign_run_platform_admin_allowed(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(async_db_session, company_id=2001, title_suffix="platform")

    resp = await async_client.post(
        f"/api/v1/campaigns/{campaign.id}/run",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_campaign_run_superuser_allowed(async_client, async_db_session):
    campaign = await _seed_campaign(async_db_session, company_id=2002, title_suffix="superuser")

    resp = await async_client.post(
        f"/api/v1/campaigns/{campaign.id}/run",
        headers=_superuser_headers_without_company(),
    )
    assert resp.status_code == 200, resp.text
