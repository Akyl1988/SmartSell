import pytest


@pytest.mark.asyncio
async def test_openapi_hides_legacy_api_and_exposes_v1(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()
    paths = payload.get("paths", {})

    assert "/api/auth/login" not in paths
    assert "/api/v1/auth/login" in paths

    assert "/api/health" not in paths
    assert "/api/v1/health" in paths
    assert "/health" in paths

    assert "/api/wallet/health" not in paths
    assert "/api/v1/wallet/health" in paths


@pytest.mark.asyncio
async def test_openapi_kaspi_catalog_template_has_binary_types(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()
    responses = (
        payload.get("paths", {})
        .get("/api/v1/kaspi/catalog/template", {})
        .get("get", {})
        .get("responses", {})
        .get("200", {})
    )
    content = responses.get("content", {})

    assert "text/csv" in content
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in content
