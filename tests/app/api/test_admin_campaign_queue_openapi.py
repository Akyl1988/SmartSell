from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_admin_campaign_queue_openapi_contract(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    payload = response.json()
    paths = payload.get("paths", {})

    queue_path = paths.get("/api/v1/admin/campaigns/queue", {})
    assert "get" in queue_path
    queue_get = queue_path["get"]
    params = {param.get("name") for param in queue_get.get("parameters", [])}

    assert "status" in params
    assert "limit" in params
    assert "companyId" in params
    assert "include_deleted" in params
    assert "company_id" not in params

    queue_responses = queue_get.get("responses", {})
    assert "200" in queue_responses

    requeue_path = paths.get("/api/v1/admin/campaigns/{campaign_id}/requeue", {})
    assert "post" in requeue_path
    assert "200" in requeue_path["post"].get("responses", {})

    cancel_path = paths.get("/api/v1/admin/campaigns/{campaign_id}/cancel", {})
    assert "post" in cancel_path
    assert "200" in cancel_path["post"].get("responses", {})
