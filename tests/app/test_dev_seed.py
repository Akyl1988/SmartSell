import os

import pytest

from app.dev.seed import ensure_dev_seed


@pytest.mark.asyncio
async def test_dev_seed_creates_user_and_login(async_db_session, async_client, monkeypatch):
    monkeypatch.setenv("SMARTSELL_DEV_SEED", "1")
    monkeypatch.setenv("SMARTSELL_IDENTIFIER", "77070000001")
    monkeypatch.setenv("SMARTSELL_PASSWORD", "devpass123")
    monkeypatch.setenv("ENVIRONMENT", "testing")

    await ensure_dev_seed(session=async_db_session)

    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": os.environ["SMARTSELL_IDENTIFIER"], "password": os.environ["SMARTSELL_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("access_token")
    assert payload.get("refresh_token")
