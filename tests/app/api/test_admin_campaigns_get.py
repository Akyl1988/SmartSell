from __future__ import annotations

import pytest

from app.models.campaign import CampaignProcessingStatus
from tests.app.test_campaign_processing_worker import _seed_campaign

pytestmark = pytest.mark.asyncio


async def test_campaign_admin_get_endpoint(async_client, async_db_session, auth_headers):
    campaign = await _seed_campaign(
        async_db_session,
        company_id=91011,
        processing_status=CampaignProcessingStatus.DONE,
    )

    resp = await async_client.get(
        f"/api/v1/admin/campaigns/{campaign.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("id") == campaign.id
    assert payload.get("processing_status") == CampaignProcessingStatus.DONE.value
    assert payload.get("attempts") == 0
