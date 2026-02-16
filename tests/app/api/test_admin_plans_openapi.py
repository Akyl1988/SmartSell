from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_admin_plans_openapi_contract(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    payload = response.json()
    paths = payload.get("paths", {})

    plans_path = paths.get("/api/v1/admin/plans", {})
    assert "get" in plans_path
    assert "post" in plans_path

    plan_detail = paths.get("/api/v1/admin/plans/{code}", {})
    assert "get" in plan_detail
    assert "patch" in plan_detail
    assert "delete" in plan_detail

    features_path = paths.get("/api/v1/admin/features", {})
    assert "get" in features_path
    assert "post" in features_path

    feature_detail = paths.get("/api/v1/admin/features/{code}", {})
    assert "get" in feature_detail
    assert "patch" in feature_detail
    assert "delete" in feature_detail

    plan_features_path = paths.get("/api/v1/admin/plan-features", {})
    assert "get" in plan_features_path

    plan_features_detail = paths.get("/api/v1/admin/plan-features/{plan_code}/{feature_code}", {})
    assert "put" in plan_features_detail
