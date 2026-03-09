from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_api_lifecycle_header_present(client):
    response = await client.get("/ping")
    assert response.status_code == 200
    assert response.headers.get("X-SmartSell-API-Version") == "v1"
