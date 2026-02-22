from __future__ import annotations

import pytest

from app.core.config import settings
from app.models.marketplace import KaspiStoreToken


class _FakeResponse:
    def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return {"importCode": "IC-OK"}


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, content=None):
        return _FakeResponse(202, '{"importCode":"IC-OK"}', {"location": "loc"})


@pytest.mark.asyncio
async def test_feed_upload_probe_available_in_dev(
    async_client,
    company_a_admin_headers,
    monkeypatch,
):
    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr("app.api.v1.kaspi.httpx.AsyncClient", _FakeAsyncClient)

    resp = await async_client.post(
        "/api/v1/kaspi/_debug/feed-upload-probe",
        headers=company_a_admin_headers,
        params={"store_name": "store-a", "paths": "/shop/api/feeds/import"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"]
    item = data["items"][0]
    assert item["path"] == "/shop/api/feeds/import"
    assert item["status_code"] == 202
    assert item["location"] == "loc"
    assert item["snippet"]


@pytest.mark.asyncio
async def test_feed_upload_probe_hidden_in_prod(
    async_client,
    company_a_admin_headers,
    monkeypatch,
):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr("app.api.v1.kaspi._is_dev_environment", lambda: False)

    resp = await async_client.post(
        "/api/v1/kaspi/_debug/feed-upload-probe",
        headers=company_a_admin_headers,
        params={"store_name": "store-a"},
    )
    assert resp.status_code == 404
