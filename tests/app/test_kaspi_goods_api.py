import httpx
import pytest

from app.models.company import Company
from app.models.marketplace import KaspiStoreToken
from app.services.kaspi_goods_client import KaspiGoodsClient, KaspiNotAuthenticated


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self._responses = kwargs.pop("responses", None) or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._responses.pop(0) if self._responses else _FakeResponse(200)


class _TimeoutAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, *args, **kwargs):
        raise httpx.ReadTimeout("timeout")


@pytest.mark.asyncio
async def test_kaspi_goods_schema_401(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    async def _raise_unauth(self):
        raise KaspiNotAuthenticated("Kaspi token is not authenticated")

    from app.services.kaspi_goods_client import KaspiGoodsClient

    monkeypatch.setattr(KaspiGoodsClient, "get_schema", _raise_unauth)

    resp = await async_client.get("/api/v1/kaspi/goods/schema", headers=company_a_admin_headers)
    assert resp.status_code == 401
    assert resp.json().get("detail") == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_kaspi_token_health_401(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.api.v1 import kaspi as kaspi_router

    fake_client = _FakeAsyncClient(responses=[_FakeResponse(401), _FakeResponse(401)])
    monkeypatch.setattr(kaspi_router.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    resp = await async_client.get("/api/v1/kaspi/token/health", headers=company_a_admin_headers)
    assert resp.status_code == 401
    assert resp.json().get("detail") == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_kaspi_goods_schema_timeout_returns_502(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.services import kaspi_goods_client as goods_client

    monkeypatch.setattr(goods_client.httpx, "AsyncClient", lambda *args, **kwargs: _TimeoutAsyncClient())

    resp = await async_client.get("/api/v1/kaspi/goods/schema", headers=company_a_admin_headers)
    assert resp.status_code == 502
    assert resp.json().get("detail") == "kaspi_upstream_unavailable"
    assert resp.json().get("code") == "HTTP_502"


@pytest.mark.asyncio
async def test_kaspi_goods_categories_timeout_returns_502(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.services import kaspi_goods_client as goods_client

    monkeypatch.setattr(goods_client.httpx, "AsyncClient", lambda *args, **kwargs: _TimeoutAsyncClient())

    resp = await async_client.get("/api/v1/kaspi/goods/categories", headers=company_a_admin_headers)
    assert resp.status_code == 502
    assert resp.json().get("detail") == "kaspi_upstream_unavailable"
    assert resp.json().get("code") == "HTTP_502"


def test_kaspi_goods_headers_builder():
    headers = KaspiGoodsClient._build_headers("token-123")
    assert headers["User-Agent"]
    assert headers["Accept"] == "application/json,text/plain,*/*"
    assert headers["Accept-Language"] == "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    assert headers["X-Auth-Token"] == "token-123"
