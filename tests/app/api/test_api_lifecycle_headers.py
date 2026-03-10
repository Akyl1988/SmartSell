from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_api_lifecycle_header_present(client):
    response = await client.get("/ping")
    assert response.status_code == 200
    assert response.headers.get("X-SmartSell-API-Version") == "v1"
    assert response.headers.get("Deprecation") is None
    assert response.headers.get("Sunset") is None


@pytest.mark.asyncio
async def test_api_lifecycle_deprecation_headers_for_deprecated_endpoint(client):
    upload_id = uuid4()
    response = await client.post(f"/api/v1/kaspi/feed/uploads/{upload_id}/refresh-status")
    assert response.status_code in {401, 403, 404, 422}
    assert response.headers.get("X-SmartSell-API-Version") == "v1"
    assert response.headers.get("Deprecation") == "true"
    assert response.headers.get("Sunset") == "Tue, 30 Jun 2026 00:00:00 GMT"
