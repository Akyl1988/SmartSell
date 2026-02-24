import pytest

from app.models.marketplace import KaspiStoreToken


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "OK"):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_kaspi_probe_platform_admin_ok(async_client, async_db_session, monkeypatch, auth_headers):
    from app.api.v1 import kaspi as kaspi_router

    async def _get_token(session, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(
        kaspi_router.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(_FakeResponse(200, "{}")),
    )

    resp = await async_client.get(
        "/api/v1/kaspi/_debug/probe",
        headers=auth_headers,
        params={"store_name": "store-a"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["status_code"] == 200
    assert data["error_class"] is None
    assert data["message"] is None
    assert isinstance(data["elapsed_ms"], int)


@pytest.mark.asyncio
async def test_kaspi_probe_requires_platform_admin(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/kaspi/_debug/probe",
        headers=company_a_admin_headers,
        params={"store_name": "store-a"},
    )
    assert resp.status_code == 403
