from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.billing import Subscription
from app.models.company import Company
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken

pytestmark = pytest.mark.asyncio


async def _set_plan(async_db_session, company_id: int, plan: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()

    res = await async_db_session.execute(
        select(Subscription).where(Subscription.company_id == company_id).where(Subscription.deleted_at.is_(None))
    )
    sub = res.scalars().first()
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(
            company_id=company_id,
            plan=plan,
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
        sub.plan = plan
        sub.status = "active"
    await async_db_session.commit()


async def _ensure_company(async_db_session, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
    company.kaspi_store_id = store_id
    await async_db_session.commit()


async def _ensure_offer(async_db_session, company_id: int, merchant_uid: str) -> None:
    offer = KaspiOffer(company_id=company_id, merchant_uid=merchant_uid, sku="SKU-1", title="Item", price=1000)
    async_db_session.add(offer)
    await async_db_session.commit()


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/api/v1/kaspi/sync/now", {"merchant_uid": "M1"}),
        ("post", "/api/v1/kaspi/feed/uploads", {"merchant_uid": "M1", "source": "public_token"}),
        ("post", "/api/v1/kaspi/feed/uploads/11111111-1111-1111-1111-111111111111/refresh", None),
        ("post", "/api/v1/kaspi/feed/uploads/11111111-1111-1111-1111-111111111111/publish", None),
        ("post", "/api/v1/kaspi/goods/imports", {"merchant_uid": "M1"}),
        ("post", "/api/v1/kaspi/goods/imports/111/refresh", None),
    ],
)
async def test_kaspi_enforcement_non_admin_forbidden(async_client, company_a_manager_headers, method, path, payload):
    request = getattr(async_client, method)
    resp = await request(path, headers=company_a_manager_headers, json=payload)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/api/v1/kaspi/sync/now", {"merchant_uid": "M1"}),
        ("post", "/api/v1/kaspi/feed/uploads", {"merchant_uid": "M1", "source": "public_token"}),
        ("post", "/api/v1/kaspi/feed/uploads/11111111-1111-1111-1111-111111111111/refresh", None),
        ("post", "/api/v1/kaspi/feed/uploads/11111111-1111-1111-1111-111111111111/publish", None),
        ("post", "/api/v1/kaspi/goods/imports", {"merchant_uid": "M1"}),
        ("post", "/api/v1/kaspi/goods/imports/111/refresh", None),
    ],
)
async def test_kaspi_enforcement_admin_missing_feature(
    async_client, async_db_session, company_a_admin_headers, method, path, payload
):
    await _set_plan(async_db_session, company_id=1001, plan="start")
    request = getattr(async_client, method)
    resp = await request(path, headers=company_a_admin_headers, json=payload)
    assert resp.status_code == 402
    data = resp.json()
    detail = data.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"
    assert detail.get("company_id") == 1001
    assert "subscription" in detail
    assert "wallet" in detail
    actions = detail.get("actions") or []
    assert any(action.get("type") == "TOPUP_WALLET" for action in actions)


async def test_kaspi_enforcement_admin_allowed_plan_goods_imports_list(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _set_plan(async_db_session, company_id=1001, plan="business")
    resp = await async_client.get("/api/v1/kaspi/goods/imports", headers=company_a_admin_headers)
    assert resp.status_code == 200


async def test_kaspi_enforcement_admin_allowed_plan_sync_now(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    from app.api.v1 import kaspi as kaspi_module

    await _set_plan(async_db_session, company_id=1001, plan="business")
    await _ensure_company(async_db_session, company_id=1001, store_id="store-a")
    await _ensure_offer(async_db_session, company_id=1001, merchant_uid="M123")

    async def _get_token(session, store_name: str):
        return "token-a"

    async def _lock_true(*args, **kwargs):
        return True

    async def _unlock(*args, **kwargs):
        return None

    async def _sync_orders(*args, **kwargs):
        return {"ok": True}

    async def _submit_import(*args, **kwargs):
        return {"code": "IC-1", "status": "UPLOADED"}

    async def _get_status(*args, **kwargs):
        return {"status": "UPLOADED"}

    def _build_xml(*args, **kwargs):
        return "<xml/>"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M123"},
    )
    assert resp.status_code == 200


async def test_kaspi_enforcement_admin_allowed_plan_feed_uploads(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    from app.api.v1 import kaspi as kaspi_module

    await _set_plan(async_db_session, company_id=1001, plan="business")
    await _ensure_company(async_db_session, company_id=1001, store_id="store-a")
    await _ensure_offer(async_db_session, company_id=1001, merchant_uid="M123")

    class _FakeKaspiAdapter:
        def feed_upload(self, *args, **kwargs):
            return {"importCode": "IC-FEED-1", "status": "received"}

        def feed_import_status(self, *args, **kwargs):
            return {"importCode": "IC-FEED-1", "status": "done"}

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(kaspi_module, "KaspiAdapter", lambda: _FakeKaspiAdapter())

    create_resp = await async_client.post(
        "/api/v1/kaspi/feed/uploads",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M123", "source": "public_token"},
    )
    assert create_resp.status_code == 200
    upload_id = create_resp.json()["id"]

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{upload_id}/refresh",
        headers=company_a_admin_headers,
    )
    assert refresh_resp.status_code == 200

    publish_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{upload_id}/publish",
        headers=company_a_admin_headers,
    )
    assert publish_resp.status_code == 200
