from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_pricing_openapi_contract(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    payload = response.json()
    paths = payload.get("paths", {})

    rules_path = paths.get("/api/v1/pricing/rules", {})
    assert "get" in rules_path
    assert "post" in rules_path

    rule_detail = paths.get("/api/v1/pricing/rules/{rule_id}", {})
    assert "get" in rule_detail
    assert "patch" in rule_detail
    assert "delete" in rule_detail

    preview_path = paths.get("/api/v1/pricing/preview", {})
    assert "post" in preview_path

    apply_path = paths.get("/api/v1/pricing/apply", {})
    assert "post" in apply_path
