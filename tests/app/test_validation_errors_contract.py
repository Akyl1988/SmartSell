import pytest


@pytest.mark.asyncio
async def test_request_validation_includes_errors(async_client):
    resp = await async_client.post("/api/v1/auth/login", json={})
    assert resp.status_code == 422
    payload = resp.json()
    assert payload.get("code") == "REQUEST_VALIDATION_ERROR"
    assert payload.get("request_id")
    errors = payload.get("errors")
    assert isinstance(errors, list)
    assert errors
