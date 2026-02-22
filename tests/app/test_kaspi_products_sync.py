import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.marketplace import KaspiStoreToken
from app.services.kaspi_products_sync_service import sync_kaspi_catalog_products


class _DummyKaspiService:
    def __init__(self, pages: dict[int, list[dict]]):
        self._pages = pages
        self.calls: list[tuple[int, int]] = []

    async def get_products(self, *, page: int = 1, page_size: int = 100, **kwargs) -> list[dict]:
        self.calls.append((page, page_size))
        return self._pages.get(page, [])


@pytest.mark.asyncio
async def test_kaspi_products_sync_pagination_and_upsert(async_db_session: AsyncSession, monkeypatch):
    company_a = Company(name="Kaspi A", kaspi_store_id="store-a")
    company_b = Company(name="Kaspi B", kaspi_store_id="store-b")
    async_db_session.add(company_a)
    async_db_session.add(company_b)
    await async_db_session.commit()

    async def _get_token(session: AsyncSession, store_name: str):
        return "token-a" if store_name == "store-a" else "token-b"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    pages_run1 = {
        1: [
            {"offer_id": "1", "name": "Item 1", "price": 10.0, "qty": 5, "is_active": True},
            {"offer_id": "2", "name": "Item 2", "price": 20.0, "qty": 2, "is_active": True},
        ],
        2: [{"offer_id": "3", "name": "Item 3", "price": 30.0, "qty": 1, "is_active": False}],
        3: [],
    }

    kaspi = _DummyKaspiService(pages_run1)
    result1 = await sync_kaspi_catalog_products(async_db_session, company_a.id, kaspi=kaspi, page_size=2, max_pages=5)
    assert result1["fetched"] == 3
    assert result1["inserted"] == 3
    assert result1["updated"] == 0

    rows_a = await async_db_session.execute(
        select(KaspiCatalogProduct).where(KaspiCatalogProduct.company_id == company_a.id)
    )
    items_a = rows_a.scalars().all()
    assert len(items_a) == 3

    rows_b = await async_db_session.execute(
        select(KaspiCatalogProduct).where(KaspiCatalogProduct.company_id == company_b.id)
    )
    items_b = rows_b.scalars().all()
    assert len(items_b) == 0

    pages_run2 = {
        1: [
            {"offer_id": "1", "name": "Item 1", "price": 11.5, "qty": 7, "is_active": True},
            {"offer_id": "2", "name": "Item 2", "price": 20.0, "qty": 2, "is_active": True},
        ],
        2: [{"offer_id": "3", "name": "Item 3", "price": 30.0, "qty": 1, "is_active": False}],
    }

    kaspi2 = _DummyKaspiService(pages_run2)
    result2 = await sync_kaspi_catalog_products(async_db_session, company_a.id, kaspi=kaspi2, page_size=2, max_pages=5)
    assert result2["fetched"] == 3
    assert result2["updated"] >= 1

    await async_db_session.rollback()
    updated_row = await async_db_session.execute(
        select(KaspiCatalogProduct)
        .where(
            KaspiCatalogProduct.company_id == company_a.id,
            KaspiCatalogProduct.offer_id == "1",
        )
        .execution_options(populate_existing=True)
    )
    updated_item = updated_row.scalars().first()
    assert updated_item is not None
    assert float(updated_item.price or 0) == 11.5
    assert updated_item.qty == 7


@pytest.mark.asyncio
async def test_kaspi_products_sync_requires_store_and_token(async_db_session: AsyncSession, monkeypatch):
    company = Company(name="Kaspi Missing")
    async_db_session.add(company)
    await async_db_session.commit()

    with pytest.raises(ValueError) as exc:
        await sync_kaspi_catalog_products(async_db_session, company.id, kaspi=_DummyKaspiService({}))
    assert "kaspi_store_not_configured" in str(exc.value)

    company.kaspi_store_id = "store-missing-token"
    await async_db_session.commit()

    async def _missing_token(session: AsyncSession, store_name: str):
        return None

    monkeypatch.setattr(KaspiStoreToken, "get_token", _missing_token)

    with pytest.raises(ValueError) as exc2:
        await sync_kaspi_catalog_products(async_db_session, company.id, kaspi=_DummyKaspiService({}))
    assert "kaspi_token_not_found" in str(exc2.value)


class _ResponseOk:
    status_code = 200
    text = "{}"

    def json(self):
        return {"products": []}


class _ResponseUnauthorized:
    status_code = 401
    text = "{}"


class _FakeProductsClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_kaspi_get_products_uses_shop_api_base_url(monkeypatch):
    from app.services import kaspi_service as kaspi_module

    captured: dict[str, object] = {}

    class _CaptureClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *args, **kwargs):
            captured["url"] = str(url)
            return _ResponseOk()

    monkeypatch.setattr(kaspi_module.settings, "KASPI_API_URL", "https://kaspi.kz", raising=False)
    monkeypatch.setattr(kaspi_module.httpx, "AsyncClient", lambda *args, **kwargs: _CaptureClient())

    svc = kaspi_module.KaspiService(api_key="token")
    await svc.get_products(page=1, page_size=1, company_id=1, store_name="store-a", request_id="req-1")

    url = captured.get("url")
    assert isinstance(url, str)
    assert "/shop/api/products" in url


@pytest.mark.asyncio
async def test_kaspi_products_client_config(monkeypatch):
    from app.services import kaspi_service as kaspi_module

    client_kwargs: dict[str, object] = {}

    def _client_factory(*args, **kwargs):
        client_kwargs.update(kwargs)
        return _FakeProductsClient(_ResponseOk())

    monkeypatch.setattr(kaspi_module.httpx, "AsyncClient", _client_factory)

    svc = kaspi_module.KaspiService(api_key="token", base_url="https://kaspi.kz")
    await svc.get_products(page=1, page_size=1, company_id=1, store_name="store-a", request_id="req-1")

    assert client_kwargs.get("http2") is False
    assert client_kwargs.get("trust_env") is False
    headers = client_kwargs.get("headers") or {}
    assert headers.get("Connection") == "close"
    assert headers.get("User-Agent")
    limits = client_kwargs.get("limits")
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == 1
    assert limits.max_keepalive_connections == 0


@pytest.mark.asyncio
async def test_kaspi_get_products_uses_x_auth_token_header(monkeypatch):
    from app.services import kaspi_service as kaspi_module

    captured: dict[str, object] = {}

    class _CaptureClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return _ResponseUnauthorized()

    monkeypatch.setattr(kaspi_module.httpx, "AsyncClient", lambda *args, **kwargs: _CaptureClient())

    svc = kaspi_module.KaspiService(api_key="token", base_url="https://kaspi.kz")
    with pytest.raises(kaspi_module.KaspiProductsUpstreamError) as exc:
        await svc.get_products(page=1, page_size=1, company_id=1, store_name="store-a", request_id="req-1")

    assert exc.value.code == "NOT_AUTHENTICATED"
    headers = captured.get("headers")
    assert isinstance(headers, dict)
    assert headers.get("X-Auth-Token") == "token"
    assert "Authorization" not in headers
    assert headers.get("Accept") == "application/json"


@pytest.mark.asyncio
async def test_kaspi_products_sync_returns_not_supported(async_client, async_db_session, company_a_admin_headers):
    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001")
        async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post("/api/v1/kaspi/products/sync", headers=company_a_admin_headers)
    assert resp.status_code == 409
    payload = resp.json()
    assert payload.get("code") == "catalog_pull_not_supported"
    assert payload.get("detail") == "catalog_pull_not_supported"
    assert payload.get("errors")


@pytest.mark.asyncio
async def test_kaspi_products_import_status_endpoint(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    from app.models.company import Company
    from app.models.marketplace import KaspiStoreToken
    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session: AsyncSession, store_name: str):  # noqa: ARG001
        return "token-a"

    async def _get_status(self, import_code: str):  # noqa: ANN001
        assert import_code == "IC-1"
        return {"status": "UPLOADED"}

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(KaspiGoodsImportClient, "get_status", _get_status)

    resp = await async_client.get("/api/v1/kaspi/products/import?i=IC-1", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["import_code"] == "IC-1"
    assert data["status"] == "UPLOADED"


@pytest.mark.asyncio
async def test_kaspi_products_import_result_endpoint(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    from app.models.company import Company
    from app.models.marketplace import KaspiStoreToken
    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session: AsyncSession, store_name: str):  # noqa: ARG001
        return "token-a"

    async def _get_result(self, import_code: str):  # noqa: ANN001
        assert import_code == "IC-2"
        return {"status": "DONE"}

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(KaspiGoodsImportClient, "get_result", _get_result)

    resp = await async_client.get("/api/v1/kaspi/products/import/result?i=IC-2", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["import_code"] == "IC-2"
    assert data["status"] == "DONE"


@pytest.mark.asyncio
async def test_kaspi_products_import_client_headers(monkeypatch):
    from app.services import kaspi_goods_import_client as import_module

    captured: dict[str, object] = {}

    class _DummyResponse:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"status": "OK"}

        def raise_for_status(self) -> None:
            return None

    class _DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None, params=None, content=None):
            captured["headers"] = headers
            captured["url"] = url
            captured["params"] = params
            return _DummyResponse()

    monkeypatch.setattr(import_module.httpx, "AsyncClient", lambda *args, **kwargs: _DummyClient())

    client = import_module.KaspiGoodsImportClient(token="token", base_url="https://kaspi.kz")
    await client.get_status(import_code="IC-10")

    headers = captured.get("headers")
    assert isinstance(headers, dict)
    assert headers.get("X-Auth-Token") == "token"
    assert "application/json" in (headers.get("Accept") or "")
