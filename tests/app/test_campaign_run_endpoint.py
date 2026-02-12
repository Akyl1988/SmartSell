from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.core.security import create_access_token, get_password_hash
from app.models.campaign import Campaign, CampaignStatus
from app.models.company import Company
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _platform_admin_headers_without_company() -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990002").first()
        if not user:
            user = User(
                phone="+79999990002",
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


def _seed_due_campaign(*, company_id: int, title_suffix: str) -> int:
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
            title=f"Manual campaign {title_suffix}",
            description="test",
            status=CampaignStatus.READY,
            scheduled_at=None,
            company_id=company.id,
        )
        s.add(campaign)
        s.commit()
        s.refresh(campaign)
        return campaign.id


async def test_campaign_run_requires_platform_admin(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"


async def test_campaign_run_ok(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()
    campaign_id = _seed_due_campaign(company_id=9101, title_suffix="ok")

    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run?company_id=9101",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("processed") == 1

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        campaign = s.query(Campaign).filter(Campaign.id == campaign_id).first()
        assert campaign is not None
        assert campaign.status == CampaignStatus.SUCCESS


async def test_campaign_run_claims_once(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()
    _seed_due_campaign(company_id=9102, title_suffix=datetime.now(UTC).isoformat())

    first = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run?company_id=9102",
        headers=headers,
    )
    assert first.status_code == 200, first.text
    payload_first = first.json()
    assert payload_first.get("processed") == 1

    second = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run?company_id=9102",
        headers=headers,
    )
    assert second.status_code == 200, second.text
    payload_second = second.json()
    assert payload_second.get("processed") == 0


async def test_campaign_run_company_id_from_body(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()
    campaign_id = _seed_due_campaign(company_id=9103, title_suffix="body")

    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run",
        headers=headers,
        json={"companyId": 9103},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("processed") == 1

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        campaign = s.query(Campaign).filter(Campaign.id == campaign_id).first()
        assert campaign is not None
        assert campaign.status == CampaignStatus.SUCCESS


async def test_campaign_run_missing_company_id_returns_400(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()

    resp = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run",
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    payload = resp.json()
    assert payload.get("code") == "company_id_required"


async def test_campaign_seed_and_run(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()

    seed = await async_client.post(
        "/api/v1/admin/dev/seed/campaign_due?company_id=9201",
        headers=headers,
    )
    assert seed.status_code == 200, seed.text
    seed_payload = seed.json()
    campaign_id = seed_payload.get("campaign_id")
    assert campaign_id

    run = await async_client.post(
        "/api/v1/admin/tasks/campaigns/run?company_id=9201",
        headers=headers,
    )
    assert run.status_code == 200, run.text
    run_payload = run.json()
    assert run_payload.get("processed") == 1

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        campaign = s.query(Campaign).filter(Campaign.id == campaign_id).first()
        assert campaign is not None
        assert campaign.status == CampaignStatus.SUCCESS


async def test_campaign_seed_without_company_id(async_client, async_db_session, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()

    await async_db_session.execute(delete(Campaign))
    await async_db_session.execute(delete(Company))
    await async_db_session.commit()

    seed = await async_client.post(
        "/api/v1/admin/dev/seed/campaign_due",
        headers=headers,
    )
    assert seed.status_code == 200, seed.text
    seed_payload = seed.json()
    campaign_id = seed_payload.get("campaign_id")
    assert campaign_id

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        campaign = s.query(Campaign).filter(Campaign.id == campaign_id).first()
        assert campaign is not None
        assert campaign.company_id is not None
