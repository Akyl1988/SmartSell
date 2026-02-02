from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.billing import Subscription
from app.models.company import Company
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken

pytestmark = pytest.mark.asyncio


async def _set_plan(async_db_session, company_id: int, plan: str) -> None:
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


def _assert_subscription_required(resp) -> None:
    assert resp.status_code == 402
    payload = resp.json()
    assert payload.get("detail") == "subscription_required"
    assert payload.get("code") == "subscription_required"
    assert payload.get("request_id")


async def _prepare_sync_now(async_db_session, monkeypatch, company_id: int, merchant_uid: str) -> None:
    from app.api.v1 import kaspi as kaspi_module

    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
    company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    offer = KaspiOffer(
        company_id=company_id,
        merchant_uid=merchant_uid,
        sku="S1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

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

    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)


async def test_kaspi_subscription_trial_blocks_kaspi_endpoints(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _set_plan(async_db_session, company_id=1001, plan="start")

    r_goods = await async_client.get(
        "/api/v1/kaspi/goods/imports",
        headers=company_a_admin_headers,
    )
    _assert_subscription_required(r_goods)

    r_feed_uploads = await async_client.get(
        "/api/v1/kaspi/feed/uploads",
        headers=company_a_admin_headers,
    )
    _assert_subscription_required(r_feed_uploads)

    r_autosync = await async_client.get(
        "/api/v1/kaspi/autosync/status",
        headers=company_a_admin_headers,
    )
    _assert_subscription_required(r_autosync)

    r_sync_now = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    _assert_subscription_required(r_sync_now)


@pytest.mark.parametrize("plan", ["basic", "pro"])
async def test_kaspi_subscription_basic_pro_allow_kaspi_endpoints(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
    plan,
):
    await _set_plan(async_db_session, company_id=1001, plan=plan)

    r_goods = await async_client.get(
        "/api/v1/kaspi/goods/imports?limit=1",
        headers=company_a_admin_headers,
    )
    assert r_goods.status_code == 200

    r_feed_uploads = await async_client.get(
        "/api/v1/kaspi/feed/uploads?limit=1",
        headers=company_a_admin_headers,
    )
    assert r_feed_uploads.status_code == 200

    r_autosync = await async_client.get(
        "/api/v1/kaspi/autosync/status",
        headers=company_a_admin_headers,
    )
    assert r_autosync.status_code == 200

    await _prepare_sync_now(async_db_session, monkeypatch, company_id=1001, merchant_uid="M1")

    r_sync_now = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert r_sync_now.status_code == 200
