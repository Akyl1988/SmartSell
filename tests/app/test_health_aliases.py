import pytest


async def _get_health_json(async_client, path: str) -> dict:
    response = await async_client.get(path, follow_redirects=True)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    return data


@pytest.mark.asyncio
async def test_health_root(async_client):
    data = await _get_health_json(async_client, "/health")
    assert "status" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_health_api_alias(async_client):
    data = await _get_health_json(async_client, "/api/health")
    assert "status" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_health_api_v1_alias(async_client):
    data = await _get_health_json(async_client, "/api/v1/health")
    assert "status" in data
    assert "version" in data
