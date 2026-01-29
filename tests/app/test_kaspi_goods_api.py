import pytest

from app.models.company import Company
from app.models.marketplace import KaspiStoreToken
from app.services.kaspi_goods_client import KaspiNotAuthenticated


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
