import io

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.kaspi_import_run import KaspiImportRun
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken
from app.models.product import Product


async def _ensure_company_store(async_db_session: AsyncSession, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}", kaspi_store_id=store_id)
        async_db_session.add(company)
    else:
        company.kaspi_store_id = store_id
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_kaspi_offers_rebuild_from_products(async_client, async_db_session, company_a_admin_headers):
    await _ensure_company_store(async_db_session, 1001, "store-a")
    async_db_session.add(
        Product(
            company_id=1001,
            name="Item 1",
            sku="SKU-1",
            price=1000,
            stock_quantity=5,
            reserved_quantity=1,
            is_active=True,
        )
    )
    await async_db_session.commit()

    resp = await async_client.post("/api/v1/kaspi/offers/rebuild", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["fetched"] == 1
    assert data["inserted"] == 1
    assert data["updated"] == 0

    res = await async_db_session.execute(
        select(KaspiOffer).where(
            KaspiOffer.company_id == 1001,
            KaspiOffer.merchant_uid == "store-a",
        )
    )
    assert len(res.scalars().all()) == 1


@pytest.mark.asyncio
async def test_kaspi_offers_import_from_csv(async_client, async_db_session, company_a_admin_headers):
    await _ensure_company_store(async_db_session, 1001, "store-a")
    csv_content = "sku,title,price,stock_count\nSKU-2,Item 2,1200,4\n"

    files = {"file": ("offers.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    resp = await async_client.post("/api/v1/kaspi/offers/import", headers=company_a_admin_headers, files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["inserted"] == 1
    assert data["merchant_uid"] == "store-a"


@pytest.mark.asyncio
async def test_products_import_start_creates_run(async_client, async_db_session, company_a_admin_headers):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    resp = await async_client.post("/api/v1/kaspi/products/import/start", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["import_code"]

    res = await async_db_session.execute(
        select(KaspiImportRun).where(
            KaspiImportRun.company_id == 1001,
            KaspiImportRun.import_code == data["import_code"],
        )
    )
    assert res.scalars().first() is not None


@pytest.mark.asyncio
async def test_products_import_upload_offers_missing(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session: AsyncSession, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    start = await async_client.post("/api/v1/kaspi/products/import/start", headers=company_a_admin_headers)
    import_code = start.json()["import_code"]

    resp = await async_client.post(
        f"/api/v1/kaspi/products/import/upload?i={import_code}", headers=company_a_admin_headers
    )
    assert resp.status_code == 409
    payload = resp.json()
    assert payload.get("code") == "offers_missing"


@pytest.mark.asyncio
async def test_products_import_upload_sends_payload(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session: AsyncSession, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    async_db_session.add(
        KaspiOffer(
            company_id=1001,
            merchant_uid="store-a",
            sku="SKU-10",
            title="Item 10",
            price=2000,
            stock_count=2,
        )
    )
    await async_db_session.commit()

    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    async def _submit_import(self, payload_json: str):  # noqa: ANN001
        assert "SKU-10" in payload_json
        return {"importCode": "IC-10", "status": "UPLOADED"}

    monkeypatch.setattr(KaspiGoodsImportClient, "submit_import", _submit_import)

    start = await async_client.post("/api/v1/kaspi/products/import/start", headers=company_a_admin_headers)
    import_code = start.json()["import_code"]

    resp = await async_client.post(
        f"/api/v1/kaspi/products/import/upload?i={import_code}", headers=company_a_admin_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["kaspi_import_code"] == "IC-10"


@pytest.mark.asyncio
async def test_products_import_run_tenant_isolation(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
    company_b_admin_headers,
):
    await _ensure_company_store(async_db_session, 1001, "store-a")
    await _ensure_company_store(async_db_session, 2001, "store-b")

    async def _get_token(session: AsyncSession, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    start = await async_client.post("/api/v1/kaspi/products/import/start", headers=company_a_admin_headers)
    import_code = start.json()["import_code"]

    resp = await async_client.post(
        f"/api/v1/kaspi/products/import/upload?i={import_code}", headers=company_b_admin_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sync_now_uses_offers_payload(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session: AsyncSession, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    async_db_session.add(
        KaspiOffer(
            company_id=1001,
            merchant_uid="store-a",
            sku="SKU-20",
            title="Item 20",
            price=2500,
            stock_count=3,
        )
    )
    await async_db_session.commit()

    from app.api.v1 import kaspi as kaspi_module
    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    async def _lock_true(*args, **kwargs):  # noqa: ANN001, ARG001
        return True

    async def _unlock(*args, **kwargs):  # noqa: ANN001, ARG001
        return None

    async def _sync_orders(*args, **kwargs):  # noqa: ANN001, ARG001
        return {"ok": True, "status": "success"}

    async def _submit_import(self, payload_json: str):  # noqa: ANN001
        assert "SKU-20" in payload_json
        return {"importCode": "IC-20", "status": "UPLOADED"}

    async def _get_status(self, import_code: str):  # noqa: ANN001
        assert import_code == "IC-20"
        return {"status": "UPLOADED"}

    def _build_xml(*args, **kwargs):  # noqa: ANN001, ARG001
        return "<xml/>"

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "store-a"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") in {"ok", "partial"}
    assert data.get("goods_import_result")
    assert data["goods_import_result"].get("kaspi_import_code") == "IC-20"
