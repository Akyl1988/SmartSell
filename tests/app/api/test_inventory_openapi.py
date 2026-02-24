from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_inventory_openapi_contract(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    payload = response.json()
    paths = payload.get("paths", {})

    reserve_path = paths.get("/api/v1/inventory/reservations/reserve", {})
    assert "post" in reserve_path

    release_path = paths.get("/api/v1/inventory/reservations/release", {})
    assert "post" in release_path

    fulfill_path = paths.get("/api/v1/inventory/reservations/fulfill", {})
    assert "post" in fulfill_path
