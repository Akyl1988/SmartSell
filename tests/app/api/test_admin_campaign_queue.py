from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.campaign import Campaign, CampaignProcessingStatus, CampaignStatus
from app.models.company import Company

pytestmark = pytest.mark.asyncio


async def _seed_campaign(
    async_db_session,
    *,
    company_id: int,
    processing_status: CampaignProcessingStatus,
    queued_at: datetime | None,
    deleted: bool = False,
    attempts: int = 0,
    last_error: str | None = None,
) -> Campaign:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    campaign = Campaign(
        title=f"Queue {company_id}-{processing_status.value}",
        description="test",
        status=CampaignStatus.READY,
        scheduled_at=None,
        company_id=company_id,
        processing_status=processing_status,
        queued_at=queued_at,
        attempts=attempts,
        last_error=last_error,
    )
    if deleted:
        campaign.deleted_at = datetime.now(UTC)
    async_db_session.add(campaign)
    await async_db_session.commit()
    await async_db_session.refresh(campaign)
    return campaign


async def test_admin_campaign_queue_list_filters_and_ordering(async_client, async_db_session, auth_headers):
    now = datetime.now(UTC)
    campaign_null = await _seed_campaign(
        async_db_session,
        company_id=8001,
        processing_status=CampaignProcessingStatus.QUEUED,
        queued_at=None,
    )
    campaign_early = await _seed_campaign(
        async_db_session,
        company_id=8001,
        processing_status=CampaignProcessingStatus.PROCESSING,
        queued_at=now - timedelta(minutes=5),
    )
    campaign_late = await _seed_campaign(
        async_db_session,
        company_id=8002,
        processing_status=CampaignProcessingStatus.FAILED,
        queued_at=now + timedelta(minutes=5),
    )
    deleted = await _seed_campaign(
        async_db_session,
        company_id=8003,
        processing_status=CampaignProcessingStatus.DONE,
        queued_at=now,
        deleted=True,
    )

    resp = await async_client.get(
        "/api/v1/admin/campaigns/queue",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    ids = [item["id"] for item in payload]
    assert deleted.id not in ids
    assert ids[:3] == [campaign_null.id, campaign_early.id, campaign_late.id]

    resp_failed = await async_client.get(
        "/api/v1/admin/campaigns/queue?status=failed",
        headers=auth_headers,
    )
    assert resp_failed.status_code == 200, resp_failed.text
    failed_ids = [item["id"] for item in resp_failed.json()]
    assert failed_ids == [campaign_late.id]

    resp_include_deleted = await async_client.get(
        "/api/v1/admin/campaigns/queue?include_deleted=true",
        headers=auth_headers,
    )
    assert resp_include_deleted.status_code == 200, resp_include_deleted.text
    include_ids = [item["id"] for item in resp_include_deleted.json()]
    assert deleted.id in include_ids

    resp_company = await async_client.get(
        f"/api/v1/admin/campaigns/queue?companyId={campaign_early.company_id}",
        headers=auth_headers,
    )
    assert resp_company.status_code == 200, resp_company.text
    company_ids = {item["company_id"] for item in resp_company.json()}
    assert company_ids == {campaign_early.company_id}

    resp_invalid = await async_client.get(
        "/api/v1/admin/campaigns/queue?status=invalid",
        headers=auth_headers,
    )
    assert resp_invalid.status_code == 400, resp_invalid.text
    assert resp_invalid.json().get("code") == "invalid_processing_status"


async def test_admin_campaign_requeue_resets_attempts_and_fields(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=8010,
        processing_status=CampaignProcessingStatus.FAILED,
        queued_at=datetime.now(UTC) - timedelta(minutes=1),
        attempts=3,
        last_error="max_attempts_exceeded",
    )
    campaign.failed_at = datetime.now(UTC) - timedelta(minutes=1)
    campaign.finished_at = campaign.failed_at
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/requeue",
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


async def test_admin_campaign_requeue_processing_conflict_without_force(
    async_client,
    async_db_session,
    auth_headers,
):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=8020,
        processing_status=CampaignProcessingStatus.PROCESSING,
        queued_at=datetime.now(UTC),
    )

    resp = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/requeue",
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    payload = resp.json()
    assert payload.get("code") == "campaign_processing_conflict"


async def test_admin_campaign_cancel_sets_failed_fields(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=8030,
        processing_status=CampaignProcessingStatus.QUEUED,
        queued_at=datetime.now(UTC),
        attempts=2,
    )

    resp = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.FAILED
    assert campaign.last_error == "cancelled_by_admin"
    assert campaign.finished_at is not None
    assert campaign.failed_at is not None
    assert campaign.attempts == 2


async def test_admin_campaign_cancel_done_conflict(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=8040,
        processing_status=CampaignProcessingStatus.DONE,
        queued_at=datetime.now(UTC),
    )

    resp = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    payload = resp.json()
    assert payload.get("code") == "campaign_already_done"


async def test_admin_campaign_cancel_noop_when_already_cancelled(
    async_client,
    async_db_session,
    auth_headers,
):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=8050,
        processing_status=CampaignProcessingStatus.FAILED,
        queued_at=datetime.now(UTC),
    )
    existing_time = datetime.now(UTC) - timedelta(minutes=2)
    campaign.last_error = "cancelled_by_admin"
    campaign.failed_at = existing_time
    campaign.finished_at = existing_time
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    await async_db_session.refresh(campaign)
    assert campaign.processing_status == CampaignProcessingStatus.FAILED
    assert campaign.last_error == "cancelled_by_admin"
    assert campaign.failed_at == existing_time
    assert campaign.finished_at == existing_time
