from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.subscriptions import plan_catalog
from app.models.billing import Subscription, WalletBalance, WalletTransaction
from app.models.company import Company
from app.services.subscriptions import renew_if_due

pytestmark = pytest.mark.asyncio


def _ceil_midnight(dt: datetime) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


async def _ensure_company(async_db_session, company_id: int, *, plan: str = "start") -> Company:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()
    company.subscription_plan = plan
    await async_db_session.commit()
    return company


async def test_manual_topup_idempotent(async_client, async_db_session, auth_headers):
    company = Company(id=9001, name="Topup Co")
    async_db_session.add(company)
    await async_db_session.commit()

    payload = {
        "companyId": company.id,
        "amount": "100.00",
        "currency": "KZT",
        "external_reference": "topup-001",
        "comment": "manual credit",
    }

    resp1 = await async_client.post("/api/v1/admin/wallet/topup", headers=auth_headers, json=payload)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()

    resp2 = await async_client.post("/api/v1/admin/wallet/topup", headers=auth_headers, json=payload)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()

    assert data2["transaction_id"] == data1["transaction_id"]
    assert data2["balance"] == data1["balance"]

    wallet = (
        await async_db_session.execute(sa.select(WalletBalance).where(WalletBalance.company_id == company.id))
    ).scalar_one()
    assert str(wallet.balance) == "100.00"


async def test_past_due_within_grace_allows_access(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    company_id = 1001
    await _ensure_company(async_db_session, company_id, plan="start")
    await async_db_session.execute(sa.delete(Subscription).where(Subscription.company_id == company_id))

    now = datetime.now(UTC)
    period_end = now - timedelta(days=1)
    grace_until = _ceil_midnight(period_end + timedelta(days=3))

    sub = Subscription(
        company_id=company_id,
        plan="business",
        status="past_due",
        billing_cycle="monthly",
        price=Decimal("30.00"),
        currency="KZT",
        started_at=period_end - timedelta(days=30),
        period_start=period_end - timedelta(days=30),
        period_end=period_end,
        next_billing_date=period_end,
        billing_anchor_day=period_end.day,
        grace_until=grace_until,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/kaspi/autosync/status", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text


async def test_after_grace_access_denied(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    company_id = 1001
    await _ensure_company(async_db_session, company_id, plan="start")
    await async_db_session.execute(sa.delete(Subscription).where(Subscription.company_id == company_id))

    now = datetime.now(UTC)
    period_end = now - timedelta(days=5)
    grace_until = _ceil_midnight(period_end + timedelta(days=3))

    sub = Subscription(
        company_id=company_id,
        plan="business",
        status="past_due",
        billing_cycle="monthly",
        price=Decimal("30.00"),
        currency="KZT",
        started_at=period_end - timedelta(days=30),
        period_start=period_end - timedelta(days=30),
        period_end=period_end,
        next_billing_date=period_end,
        billing_anchor_day=period_end.day,
        grace_until=grace_until,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/kaspi/autosync/status", headers=company_a_admin_headers)
    assert resp.status_code == 402, resp.text
    payload = resp.json()
    assert payload.get("detail") == "subscription_required"


async def test_renewal_within_grace_reactivates_and_extends(async_db_session, monkeypatch):
    monkeypatch.setitem(
        plan_catalog.PLAN_CATALOG,
        "business",
        plan_catalog.PlanCatalogEntry(
            plan_id="business",
            display_name="Business",
            price=Decimal("30.00"),
            currency="KZT",
        ),
    )

    company = Company(id=9002, name="Grace Renew Co")
    async_db_session.add(company)
    await async_db_session.flush()

    wallet = WalletBalance(company_id=company.id, balance=Decimal("100.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.flush()

    period_end = datetime(2026, 1, 31, 0, 0, tzinfo=UTC)
    grace_until = datetime(2026, 2, 4, 0, 0, tzinfo=UTC)

    sub = Subscription(
        company_id=company.id,
        plan="business",
        status="past_due",
        billing_cycle="monthly",
        price=Decimal("30.00"),
        currency="KZT",
        started_at=period_end - timedelta(days=30),
        period_start=period_end - timedelta(days=30),
        period_end=period_end,
        next_billing_date=period_end,
        billing_anchor_day=31,
        grace_until=grace_until,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    processed = await renew_if_due(async_db_session, now=datetime(2026, 2, 2, 12, 0, tzinfo=UTC))
    assert processed == 1

    await async_db_session.commit()

    await async_db_session.refresh(sub)
    await async_db_session.refresh(wallet)

    assert sub.status == "active"
    assert sub.grace_until is None
    assert sub.period_end == datetime(2026, 2, 28, 0, 0, tzinfo=UTC)
    assert str(wallet.balance) == "70.00"

    tx = (
        (await async_db_session.execute(sa.select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id)))
        .scalars()
        .all()
    )
    assert tx
