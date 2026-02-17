from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_preorders_openapi_contract(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    payload = response.json()
    paths = payload.get("paths", {})

    preorders_path = paths.get("/api/v1/preorders", {})
    assert "get" in preorders_path
    assert "post" in preorders_path

    preorder_detail = paths.get("/api/v1/preorders/{preorder_id}", {})
    assert "get" in preorder_detail
    assert "patch" in preorder_detail

    confirm_path = paths.get("/api/v1/preorders/{preorder_id}/confirm", {})
    assert "post" in confirm_path

    cancel_path = paths.get("/api/v1/preorders/{preorder_id}/cancel", {})
    assert "post" in cancel_path

    fulfill_path = paths.get("/api/v1/preorders/{preorder_id}/fulfill", {})
    assert "post" in fulfill_path
