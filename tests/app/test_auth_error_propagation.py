import pytest


@pytest.mark.asyncio
async def test_auth_me_requires_auth(async_client):
    resp = await async_client.get("/api/v1/auth/me")
    assert resp.status_code in {401, 403}
    assert resp.status_code != 500

    resp = await async_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.value"})
    assert resp.status_code in {401, 403}
    assert resp.status_code != 500


@pytest.mark.asyncio
async def test_users_me_requires_auth(async_client):
    resp = await async_client.get("/api/v1/users/me")
    assert resp.status_code in {401, 403}
    assert resp.status_code != 500

    resp = await async_client.get("/api/v1/users/me", headers={"Authorization": "Bearer invalid.token.value"})
    assert resp.status_code in {401, 403}
    assert resp.status_code != 500
