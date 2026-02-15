import importlib

import pytest


def _reset_campaigns_storage() -> None:
    campaigns_mod = importlib.import_module("app.api.v1.campaigns")
    campaigns_mod._STORAGE_INSTANCE = None
    campaigns_mod._STORAGE_BACKEND = None


@pytest.mark.asyncio
async def test_campaigns_health_reports_orm(async_client, auth_headers, monkeypatch):
    monkeypatch.setenv("SMARTSELL_CAMPAIGNS_STORAGE", "orm")
    monkeypatch.setenv("FORCE_INMEMORY_BACKENDS", "0")

    _reset_campaigns_storage()

    resp = await async_client.get("/api/v1/campaigns/health", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("storage") == "orm"
    assert data.get("status") == "ok"
    assert isinstance(data.get("campaigns"), int)
    assert isinstance(data.get("messages"), int)


@pytest.mark.asyncio
async def test_campaigns_storage_switches_to_memory(async_client, auth_headers, monkeypatch):
    monkeypatch.setenv("SMARTSELL_CAMPAIGNS_STORAGE", "memory")
    monkeypatch.setenv("FORCE_INMEMORY_BACKENDS", "0")

    _reset_campaigns_storage()

    resp = await async_client.get("/api/v1/campaigns/health", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("storage") == "memory"
    assert data.get("status") == "ok"
