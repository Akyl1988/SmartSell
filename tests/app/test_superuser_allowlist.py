from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core import config
from app.core import security as security_module
from app.models.billing import Subscription
from app.models.company import Company
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _refresh_settings(monkeypatch) -> None:
    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    refreshed = config.get_settings()
    monkeypatch.setattr(config, "settings", refreshed, raising=False)
    monkeypatch.setattr(security_module, "settings", refreshed, raising=False)


async def _ensure_start_plan(async_db_session, company_id: int) -> None:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()
    company.subscription_plan = "start"
    await async_db_session.execute(sa.delete(Subscription).where(Subscription.company_id == company_id))
    await async_db_session.commit()


async def test_superuser_allowlist_bypasses_feature_and_admin(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_manager_headers,
):
    await _ensure_start_plan(async_db_session, company_id=1001)

    res = await async_db_session.execute(sa.select(User).where(User.phone == "+70000010002"))
    user = res.scalars().first()
    assert user is not None

    monkeypatch.delenv("SUPERUSER_ALLOWLIST", raising=False)
    _refresh_settings(monkeypatch)

    resp_forbidden = await async_client.get("/api/v1/kaspi/orders", headers=company_a_manager_headers)
    assert resp_forbidden.status_code == 403

    resp_subscription = await async_client.get("/api/v1/kaspi/autosync/status", headers=company_a_manager_headers)
    assert resp_subscription.status_code == 402
    payload = resp_subscription.json()
    assert payload.get("detail") == "subscription_required"

    monkeypatch.setenv("SUPERUSER_ALLOWLIST", str(user.id))
    _refresh_settings(monkeypatch)

    resp_orders = await async_client.get("/api/v1/kaspi/orders", headers=company_a_manager_headers)
    assert resp_orders.status_code == 200

    resp_autosync = await async_client.get("/api/v1/kaspi/autosync/status", headers=company_a_manager_headers)
    assert resp_autosync.status_code == 200

    monkeypatch.delenv("SUPERUSER_ALLOWLIST", raising=False)
    _refresh_settings(monkeypatch)


async def test_kaspi_sync_now_subscription_required_for_normal_user(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
):
    await _ensure_start_plan(async_db_session, company_id=1001)

    monkeypatch.delenv("SUPERUSER_ALLOWLIST", raising=False)
    _refresh_settings(monkeypatch)

    resp = await async_client.post(
        "/api/v1/kaspi/sync/now",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M1"},
    )
    assert resp.status_code == 402
    payload = resp.json()
    assert payload.get("detail") == "subscription_required"
    assert payload.get("code") == "subscription_required"
