import pytest
import sqlalchemy as sa

from app.models.company import Company
from app.models.marketplace import KaspiStoreToken


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.content = b""
        self.text = ""


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse], timeout=None):
        self._responses = responses
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_kaspi_token_selftest_goods_hint(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async_db_session.add(KaspiStoreToken(store_name="store-a", token_ciphertext=b"token-a"))
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.api.v1 import kaspi as kaspi_router

    timeouts: dict[str, object] = {}

    fake_client = _FakeAsyncClient(
        [
            _FakeResponse(200),
            _FakeResponse(401),
            _FakeResponse(401),
        ]
    )

    def _client_factory(*args, **kwargs):
        timeouts["value"] = kwargs.get("timeout")
        return fake_client

    monkeypatch.setattr(kaspi_router.httpx, "AsyncClient", _client_factory)

    resp = await async_client.get("/api/v1/kaspi/token/selftest", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["orders_http"] == 200
    assert data["goods_schema_http"] == 401
    assert data["goods_categories_http"] == 401
    assert data["goods_access"] == "missing_or_not_enabled"

    row = (
        (
            await async_db_session.execute(
                sa.select(KaspiStoreToken).where(sa.func.lower(KaspiStoreToken.store_name) == "store-a")
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.last_selftest_at is not None
    assert row.last_selftest_status == "ok"
    assert timeouts.get("value") == 5.0


@pytest.mark.asyncio
async def test_kaspi_token_selftest_orders_unauthorized(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async_db_session.add(KaspiStoreToken(store_name="store-a", token_ciphertext=b"token-a"))
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.api.v1 import kaspi as kaspi_router

    fake_client = _FakeAsyncClient(
        [
            _FakeResponse(401),
            _FakeResponse(200),
            _FakeResponse(200),
        ]
    )
    monkeypatch.setattr(kaspi_router.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    resp = await async_client.get("/api/v1/kaspi/token/selftest", headers=company_a_admin_headers)
    assert resp.status_code == 401
    assert resp.json().get("detail") == "NOT_AUTHENTICATED"

    row = (
        (
            await async_db_session.execute(
                sa.select(KaspiStoreToken).where(sa.func.lower(KaspiStoreToken.store_name) == "store-a")
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.last_selftest_status == "invalid_token"
    assert row.last_selftest_error_code == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_kaspi_token_selftest_upstream_unavailable(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async_db_session.add(KaspiStoreToken(store_name="store-a", token_ciphertext=b"token-a"))
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.api.v1 import kaspi as kaspi_router

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise kaspi_router.httpx.RequestError("boom")

    monkeypatch.setattr(kaspi_router.httpx, "AsyncClient", lambda *args, **kwargs: _FailingClient())

    resp = await async_client.get("/api/v1/kaspi/token/selftest", headers=company_a_admin_headers)
    assert resp.status_code == 502
    assert resp.json().get("detail") == "upstream_unavailable"

    row = (
        (
            await async_db_session.execute(
                sa.select(KaspiStoreToken).where(sa.func.lower(KaspiStoreToken.store_name) == "store-a")
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.last_selftest_status == "upstream_unavailable"
