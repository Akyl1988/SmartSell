from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.billing import Subscription
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


async def _ensure_subscription_plan(async_db_session, company_id: int, plan: str) -> None:
    existing_company = await async_db_session.get(Company, company_id)
    if not existing_company:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.flush()

    res = await async_db_session.execute(
        select(Subscription).where(Subscription.company_id == company_id).where(Subscription.deleted_at.is_(None))
    )
    sub = res.scalars().first()
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(
            company_id=company_id,
            plan=normalize_plan_id(plan) or "trial",
            status="active",
            billing_cycle="monthly",
            price=Decimal("0.00"),
            currency="KZT",
            started_at=now,
            period_start=now,
            period_end=now + timedelta(days=30),
            next_billing_date=now + timedelta(days=31),
        )
        async_db_session.add(sub)
    else:
        sub.plan = normalize_plan_id(plan) or "trial"
        sub.status = "active"
    await async_db_session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _ensure_sync_now_subscription(async_db_session, request):
    if "company_a_admin_headers" not in request.fixturenames:
        return
    await _ensure_subscription_plan(async_db_session, company_id=1001, plan="basic")


@pytest.mark.asyncio
async def test_kaspi_sync_now_lock_prevents_parallel(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    from app.api.v1 import kaspi as kaspi_module

    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

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
async def test_kaspi_sync_now_trial_requires_subscription(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _ensure_subscription_plan(async_db_session, company_id=1001, plan="start")
    await _ensure_company(async_db_session, 1001, "store-a")

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 402
    payload = resp.json()
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"
    assert payload.get("request_id")


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
    assert data["status"] == "ok"
    assert data["errors"] == []
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
        await asyncio.sleep(999)
        return {"ok": True}

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
        "/api/v1/kaspi/sync/now?timeout_sec=25",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "partial"
    assert data["orders_sync"]["status"] == "timeout"
    assert data["orders_sync"]["code"] == "upstream_timeout"
    assert data["orders_sync"]["detail"] == "Kaspi orders sync timed out"
    assert data["errors"][0]["code"] == "upstream_timeout"
    assert data["errors"][0]["detail"] == "Kaspi orders sync timed out"
    assert data.get("phase")
    assert data["errors"][0].get("phase")
    assert data["errors"][0]["request_id"]
    assert data["goods_import_result"]["status"] == "success"
    assert data["offers_feed_result"]["status"] == "success"


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
    from app.services.kaspi_service import KaspiService

    order = []

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _fetch_orders_page(*args, **kwargs):
        request = httpx.Request("GET", "https://kaspi.kz/shop/api/v2/orders")
        raise httpx.ReadTimeout("read timeout", request=request)

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
    monkeypatch.setattr(KaspiService, "_fetch_orders_page", _fetch_orders_page)
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
    assert data["status"] == "partial"
    assert data["orders_sync"]["status"] == "timeout"
    assert data["orders_sync"]["code"] == "upstream_timeout"
    assert data["orders_sync"]["detail"] == "Kaspi orders sync timed out"
    assert data["goods_import_result"]["status"] == "success"
    assert data["offers_feed_result"]["status"] == "success"
    assert data.get("phase")
    assert data["errors"][0].get("phase")
    assert order == ["goods_import", "goods_refresh", "feed"]


@pytest.mark.asyncio
async def test_kaspi_sync_now_timeout_is_bounded(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    from app.api.v1 import kaspi as kaspi_module

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _sync_orders(*args, **kwargs):
        await asyncio.sleep(999)
        return {"ok": True}

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="M1",
        sku="S1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    submit_called: list[bool] = []

    async def _submit_import(*args, **kwargs):
        submit_called.append(True)
        await asyncio.sleep(999)
        return {"code": "IC-1", "status": "UPLOADED"}

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    start = time.monotonic()
    resp = await asyncio.wait_for(
        async_client.post(
            "/api/v1/kaspi/sync/now?timeout_sec=0.2",
            headers=company_a_admin_headers,
            json={"merchant_uid": "M1", "refresh_once": False},
        ),
        timeout=1.0,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "partial"
    assert data.get("phase") == "goods_import"
    assert data["errors"][0]["code"] == "kaspi_sync_timeout"
    assert data["errors"][0]["detail"]
    assert data["errors"][0]["phase"] == "goods_import"
    assert data["errors"][0]["request_id"]
    goods_import_result = data.get("goods_import_result") or {}
    if goods_import_result.get("status") == "skipped":
        assert not submit_called
    else:
        assert submit_called


@pytest.mark.asyncio
async def test_kaspi_sync_now_timeout_hard_returns_504(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    from app.api.v1 import kaspi as kaspi_module

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _sync_orders(*args, **kwargs):
        await asyncio.sleep(999)
        return {"ok": True}

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="M1",
        sku="S1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    async def _submit_import(*args, **kwargs):
        await asyncio.sleep(999)
        return {"code": "IC-1", "status": "UPLOADED"}

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    resp = await asyncio.wait_for(
        async_client.post(
            "/api/v1/kaspi/sync/now?timeout_sec=0.2&hard=1",
            headers=company_a_admin_headers,
            json={"merchant_uid": "M1", "refresh_once": False},
        ),
        timeout=1.0,
    )

    assert resp.status_code == 504
    data = resp.json()
    assert data.get("code") == "kaspi_sync_timeout"


def test_kaspi_sync_now_budget_min_orders_timeout():
    from app.api.v1 import kaspi as kaspi_module

    budgets = kaspi_module._compute_sync_now_budgets(20.0)
    assert budgets["final_orders_timeout"] >= 10.0
    assert budgets["final_orders_timeout"] <= budgets["budget_total"]


def test_kaspi_sync_now_budget_grows_with_timeout():
    from app.api.v1 import kaspi as kaspi_module

    budgets = kaspi_module._compute_sync_now_budgets(60.0)
    assert budgets["final_orders_timeout"] > 12.0
    assert budgets["final_orders_timeout"] <= budgets["budget_total"]


@pytest.mark.asyncio
async def test_kaspi_sync_now_query_only_accepts(
    async_client, async_db_session, monkeypatch, company_a_admin_headers, caplog
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "secret-token"

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
        return {"ok": True, "status": "success"}

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
        "/api/v1/kaspi/sync/now?timeout_sec=25&merchantUid=M1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200
    assert "secret-token" not in caplog.text
