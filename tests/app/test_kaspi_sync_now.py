from __future__ import annotations

import pytest

from app.models.company import Company
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken


async def _ensure_company(async_db_session, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
    company.kaspi_store_id = store_id
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_kaspi_sync_now_lock_prevents_parallel(async_client, monkeypatch, company_a_admin_headers):
    from app.api.v1 import kaspi as kaspi_module

    async def _lock_false(*args, **kwargs):
        return False

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_false)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 409
    data = resp.json()
    assert data.get("detail") == "kaspi_sync_in_progress"
    assert data.get("code") == "kaspi_sync_in_progress"


@pytest.mark.asyncio
async def test_kaspi_sync_now_order_flow(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="M1",
        sku="S1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    order = []

    from app.api.v1 import kaspi as kaspi_module

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _sync_orders(*args, **kwargs):
        order.append("orders")
        return {"ok": True}

    async def _submit_import(*args, **kwargs):
        order.append("goods_import")
        return {"code": "IC-1", "status": "UPLOADED"}

    async def _get_status(*args, **kwargs):
        order.append("goods_refresh")
        return {"status": "UPLOADED"}

    def _build_xml(*args, **kwargs):
        order.append("feed")
        return "<xml/>"

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["goods_import_code"] == "IC-1"
    assert order == ["orders", "goods_import", "goods_refresh", "feed"]


@pytest.mark.asyncio
async def test_kaspi_sync_now_orders_timeout_returns_200(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="M1",
        sku="S1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    from app.api.v1 import kaspi as kaspi_module

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _sync_orders(*args, **kwargs):
        raise TimeoutError("timeout")

    async def _submit_import(*args, **kwargs):
        return {"code": "IC-1", "status": "UPLOADED"}

    async def _get_status(*args, **kwargs):
        return {"status": "UPLOADED"}

    def _build_xml(*args, **kwargs):
        return "<xml/>"

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["orders_sync"]["status"] == "timeout"


@pytest.mark.asyncio
async def test_kaspi_sync_now_orders_read_timeout_code(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="M1",
        sku="S1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    import httpx

    from app.api.v1 import kaspi as kaspi_module

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _sync_orders(*args, **kwargs):
        request = httpx.Request("GET", "https://kaspi.kz/shop/api/v2/orders")
        raise httpx.ReadTimeout("read timeout", request=request)

    async def _submit_import(*args, **kwargs):
        return {"code": "IC-1", "status": "UPLOADED"}

    async def _get_status(*args, **kwargs):
        return {"status": "UPLOADED"}

    def _build_xml(*args, **kwargs):
        return "<xml/>"

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["orders_sync"]["code"] == "read_timeout"
