from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.core.security import create_access_token, get_password_hash
from app.models import Company, User
from app.models.billing import Subscription
from app.models.kaspi_offer import KaspiOffer

pytestmark = pytest.mark.asyncio


def _auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, extra={"company_id": user.company_id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _platform_headers(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, extra={"role": "platform_admin"})
    return {"Authorization": f"Bearer {token}"}


async def _make_user(session, *, company: Company, phone: str, role: str) -> User:
    user = User(
        company_id=company.id,
        phone=phone,
        hashed_password=get_password_hash("Secret123!"),
        role=role,
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _clear_subscriptions(session, company_id: int) -> None:
    await session.execute(sa.delete(Subscription).where(Subscription.company_id == company_id))
    await session.commit()


async def test_kaspi_subscription_override_bypasses_gate(
    async_client,
    async_db_session,
    monkeypatch,
):
    from app.api.v1 import kaspi as kaspi_module

    company = Company(name="Override Co", subscription_plan="start", kaspi_store_id="store-a")
    async_db_session.add(company)
    await async_db_session.flush()

    owner = await _make_user(async_db_session, company=company, phone="77000050001", role="admin")
    platform_admin = User(
        phone="77000900001",
        company_id=None,
        hashed_password=get_password_hash("Secret123!"),
        role="platform_admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(platform_admin)
    company.owner_id = owner.id
    await async_db_session.commit()
    await async_db_session.refresh(platform_admin)

    await _clear_subscriptions(async_db_session, company.id)

    merchant_uid = "M-OVR-1"

    async_db_session.add(
        KaspiOffer(
            company_id=company.id,
            merchant_uid=merchant_uid,
            sku="SKU-OVR-1",
            title="Item",
            price=1000,
        )
    )
    await async_db_session.commit()

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

    monkeypatch.setattr(kaspi_module.KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(kaspi_module, "_try_sync_now_lock", _lock_true)
    monkeypatch.setattr(kaspi_module, "_release_sync_now_lock", _unlock)
    monkeypatch.setattr(kaspi_module.KaspiService, "sync_orders", _sync_orders)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "submit_import", _submit_import)
    monkeypatch.setattr(kaspi_module.KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(kaspi_module, "_build_kaspi_offers_xml", _build_xml)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=_auth_headers(owner),
        json={"merchant_uid": merchant_uid},
    )
    assert resp.status_code == 402

    upsert = await async_client.put(
        f"/api/v1/admin/subscription-overrides/kaspi/{merchant_uid}",
        headers=_platform_headers(platform_admin),
        json={"note": "override", "company_id": company.id},
    )
    assert upsert.status_code == 200

    ok = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=_auth_headers(owner),
        json={"merchant_uid": merchant_uid},
    )
    assert ok.status_code == 402

    expired_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    expired = await async_client.put(
        f"/api/v1/admin/subscription-overrides/kaspi/{merchant_uid}",
        headers=_platform_headers(platform_admin),
        json={"active_until": expired_at, "note": "expired", "company_id": company.id},
    )
    assert expired.status_code == 200

    blocked = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=_auth_headers(owner),
        json={"merchant_uid": merchant_uid},
    )
    assert blocked.status_code == 402


async def test_subscription_override_owner_only(async_client, async_db_session):
    company = Company(name="Owner Only", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.flush()

    owner = await _make_user(async_db_session, company=company, phone="77000050002", role="admin")
    company.owner_id = owner.id
    await async_db_session.commit()

    admin = await _make_user(async_db_session, company=company, phone="77000050003", role="admin")

    resp_put = await async_client.put(
        "/api/v1/admin/subscription-overrides/kaspi/M-OVR-2",
        headers=_auth_headers(admin),
        json={"note": "nope"},
    )
    assert resp_put.status_code == 403
    payload_put = resp_put.json()
    assert payload_put.get("code") == "ADMIN_REQUIRED"

    resp_del = await async_client.delete(
        "/api/v1/admin/subscription-overrides/kaspi/M-OVR-2",
        headers=_auth_headers(admin),
    )
    assert resp_del.status_code == 403
    payload_del = resp_del.json()
    assert payload_del.get("code") == "ADMIN_REQUIRED"


async def test_subscription_override_tenant_isolation(async_client, async_db_session):
    company_a = Company(name="Tenant A", subscription_plan="start")
    company_b = Company(name="Tenant B", subscription_plan="start")
    async_db_session.add_all([company_a, company_b])
    await async_db_session.flush()

    owner_a = await _make_user(async_db_session, company=company_a, phone="77000050004", role="admin")
    owner_b = await _make_user(async_db_session, company=company_b, phone="77000050005", role="admin")
    platform_admin = User(
        phone="77000900002",
        company_id=None,
        hashed_password=get_password_hash("Secret123!"),
        role="platform_admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(platform_admin)
    company_a.owner_id = owner_a.id
    company_b.owner_id = owner_b.id
    await async_db_session.commit()
    await async_db_session.refresh(platform_admin)

    await async_client.put(
        "/api/v1/admin/subscription-overrides/kaspi/M-OVR-3",
        headers=_platform_headers(platform_admin),
        json={"note": "tenant-a", "company_id": company_a.id},
    )

    resp = await async_client.delete(
        f"/api/v1/admin/subscription-overrides/kaspi/M-OVR-3?companyId={company_a.id}",
        headers=_auth_headers(owner_b),
    )
    assert resp.status_code == 403
