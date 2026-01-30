import httpx
import pytest
import sqlalchemy as sa

from app.models.company import Company
from app.models.kaspi_goods_import import KaspiGoodsImport
from app.models.marketplace import KaspiStoreToken


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.content = b"{}"

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "https://kaspi.kz"),
                response=httpx.Response(self.status_code),
            )


class _FakeAsyncClient:
    def __init__(self, responses, recorder):
        self._responses = responses
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, params=None, json=None, files=None):
        self._recorder.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params,
                "json": json,
                "files": files,
            }
        )
        return self._responses.pop(0) if self._responses else _FakeResponse(200)


async def _ensure_company(async_db_session, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
    company.kaspi_store_id = store_id
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_kaspi_goods_import_create_and_refresh(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    recorder: list[dict] = []
    responses = [
        _FakeResponse(200, {"importCode": "IC-1", "status": "submitted"}),
    ]
    from app.services import kaspi_goods_client

    monkeypatch.setattr(
        kaspi_goods_client.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, recorder),
    )

    payload = [{"sku": "S1", "name": "Item 1", "price": 1000}]
    resp = await async_client.post(
        "/api/v1/kaspi/goods/imports",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"payload": payload},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["import_code"] == "IC-1"
    assert data["status"] == "submitted"

    assert recorder[0]["method"] == "POST"
    assert recorder[0]["url"].endswith("/shop/api/products/import")
    assert recorder[0]["headers"]["X-Auth-Token"] == "token-a"
    assert recorder[0]["headers"]["Content-Type"] == "text/plain"
    assert recorder[0]["json"] == payload

    import_id = data["id"]

    recorder.clear()
    responses = [
        _FakeResponse(200, {"status": "processing"}),
        _FakeResponse(200, {"status": "done", "items": []}),
    ]
    monkeypatch.setattr(
        kaspi_goods_client.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, recorder),
    )

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/goods/imports/{import_id}/refresh",
        headers=company_a_admin_headers,
    )
    assert refresh_resp.status_code == 200
    refresh_data = refresh_resp.json()
    assert refresh_data["status"] == "processing"
    assert refresh_data["status_json"]["status"] == "processing"
    assert refresh_data["result_json"]["status"] == "done"

    assert recorder[0]["method"] == "GET"
    assert recorder[0]["url"].endswith("/shop/api/products/import")
    assert recorder[0]["params"] == {"i": "IC-1"}

    assert recorder[1]["method"] == "GET"
    assert recorder[1]["url"].endswith("/shop/api/products/import/result")
    assert recorder[1]["params"] == {"i": "IC-1"}


@pytest.mark.asyncio
async def test_kaspi_goods_import_tenant_isolation(
    async_client,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")
    await _ensure_company(async_db_session, 2001, "store-b")

    record = KaspiGoodsImport(
        company_id=2001,
        merchant_uid="M2",
        import_code="IC-B",
        status="submitted",
        request_json=[{"sku": "S1"}],
    )
    async_db_session.add(record)
    await async_db_session.commit()
    await async_db_session.refresh(record)

    resp = await async_client.get(
        f"/api/v1/kaspi/goods/imports/{record.id}",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 404

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/goods/imports/{record.id}/refresh",
        headers=company_a_admin_headers,
    )
    assert refresh_resp.status_code == 404


@pytest.mark.asyncio
async def test_kaspi_goods_import_handles_upstream_error(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    async def _raise_error(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "error",
            request=httpx.Request("POST", "https://kaspi.kz"),
            response=httpx.Response(500),
        )

    from app.services.kaspi_goods_client import KaspiGoodsClient

    monkeypatch.setattr(KaspiGoodsClient, "post_import", _raise_error)

    resp = await async_client.post(
        "/api/v1/kaspi/goods/imports",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        json={"payload": [{"sku": "S1"}]},
    )
    assert resp.status_code == 502
    assert resp.json().get("detail") == "kaspi_upstream_error"


@pytest.mark.asyncio
async def test_kaspi_goods_import_upload_mvp(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    recorder: list[dict] = []
    responses = [_FakeResponse(200, {"importCode": "IC-UP-1", "status": "submitted"})]

    from app.services import kaspi_goods_client

    monkeypatch.setattr(
        kaspi_goods_client.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, recorder),
    )

    files = {"file": ("goods.csv", b"sku,name\nS1,Item\n", "text/csv")}
    resp = await async_client.post(
        "/api/v1/kaspi/goods/import/upload",
        headers=company_a_admin_headers,
        params={"merchantUid": "M1"},
        files=files,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["import_code"] == "IC-UP-1"
    assert data["status"] == "submitted"

    assert recorder[0]["method"] == "POST"
    assert recorder[0]["url"].endswith("/shop/api/products/import")
    assert recorder[0]["headers"]["X-Auth-Token"] == "token-a"
    assert recorder[0]["files"]["file"][0] == "goods.csv"

    record = (
        (
            await async_db_session.execute(
                sa.select(KaspiGoodsImport).where(
                    sa.and_(KaspiGoodsImport.company_id == 1001, KaspiGoodsImport.import_code == "IC-UP-1")
                )
            )
        )
        .scalars()
        .first()
    )
    assert record is not None
    assert record.filename == "goods.csv"
    assert record.merchant_uid == "M1"
    assert "IC-UP-1" in (record.raw_response or "")


@pytest.mark.asyncio
async def test_kaspi_goods_import_status_by_code(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    recorder: list[dict] = []
    responses = [_FakeResponse(200, {"status": "processing"})]

    from app.services import kaspi_goods_client

    monkeypatch.setattr(
        kaspi_goods_client.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, recorder),
    )

    resp = await async_client.get(
        "/api/v1/kaspi/goods/import/status",
        headers=company_a_admin_headers,
        params={"importCode": "IC-STATUS-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["import_code"] == "IC-STATUS-1"
    assert data["status"] == "processing"

    assert recorder[0]["method"] == "GET"
    assert recorder[0]["url"].endswith("/shop/api/products/import/status")
    assert recorder[0]["params"] == {"importCode": "IC-STATUS-1"}
