from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.subscriptions import plan_catalog
from app.models.billing import Subscription, WalletBalance, WalletTransaction
from app.models.company import Company
from app.services.subscriptions import activate_plan, renew_if_due


@pytest.mark.asyncio
async def test_manual_topup_increases_balance_and_records_ledger(
    async_client,
    async_db_session,
    auth_headers,
):
    company = Company(id=9001, name="Topup Co")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    resp = await async_client.post(
        "/api/v1/admin/wallet/topup",
        headers=auth_headers,
        json={
            "companyId": company.id,
            "amount": "100.00",
            "currency": "KZT",
            "external_reference": "topup-001",
            "comment": "manual credit",
        },
    )
    assert resp.status_code == 200, resp.text

    wallet = await WalletBalance.get_for_company_async(async_db_session, company.id)
    await async_db_session.refresh(wallet)
    assert wallet.balance == Decimal("100.00")

    row = (
        (await async_db_session.execute(sa.select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id)))
        .scalars()
        .first()
    )
    assert row is not None
    assert row.transaction_type == "manual_topup"
    assert row.client_request_id == "topup-001"


@pytest.mark.asyncio
async def test_activate_plan_charges_wallet_and_sets_period(async_db_session, monkeypatch):
    monkeypatch.setitem(
        plan_catalog.PLAN_CATALOG,
        "business",
        plan_catalog.PlanCatalogEntry(
            plan_id="business",
            display_name="Business",
            price=Decimal("50.00"),
            currency="KZT",
        ),
    )

    company = Company(id=9002, name="Billing Co")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    wallet = WalletBalance(company_id=company.id, balance=Decimal("100.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.commit()
    await async_db_session.refresh(wallet)

    now = datetime(2026, 2, 5, 12, 0, tzinfo=UTC)
    sub = await activate_plan(async_db_session, company_id=company.id, plan_code="business", now=now)
    await async_db_session.commit()
    await async_db_session.refresh(sub)
    await async_db_session.refresh(wallet)

    assert sub.status == "active"
    assert sub.period_start == now
    assert sub.period_end == datetime(2026, 3, 5, 12, 0, tzinfo=UTC)
    assert wallet.balance == Decimal("50.00")


@pytest.mark.asyncio
async def test_renewal_extends_period_when_balance_sufficient(async_db_session, monkeypatch):
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

    company = Company(id=9003, name="Renew Co")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    wallet = WalletBalance(company_id=company.id, balance=Decimal("100.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.flush()

    period_end = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    sub = Subscription(
        company_id=company.id,
        plan="business",
        status="active",
        billing_cycle="monthly",
        price=Decimal("30.00"),
        currency="KZT",
        started_at=period_end - timedelta(days=30),
        period_start=period_end - timedelta(days=30),
        period_end=period_end,
        next_billing_date=period_end,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    processed = await renew_if_due(async_db_session, now=datetime(2026, 2, 5, 0, 0, tzinfo=UTC))
    await async_db_session.commit()
    await async_db_session.refresh(sub)
    await async_db_session.refresh(wallet)

    assert processed == 1
    assert sub.status == "active"
    assert sub.period_end == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    assert wallet.balance == Decimal("70.00")


@pytest.mark.asyncio
async def test_renewal_marks_past_due_when_insufficient_balance(async_db_session, monkeypatch):
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

    company = Company(id=9004, name="Past Due Co")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    wallet = WalletBalance(company_id=company.id, balance=Decimal("10.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.flush()

    period_end = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    sub = Subscription(
        company_id=company.id,
        plan="business",
        status="active",
        billing_cycle="monthly",
        price=Decimal("30.00"),
        currency="KZT",
        started_at=period_end - timedelta(days=30),
        period_start=period_end - timedelta(days=30),
        period_end=period_end,
        next_billing_date=period_end,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    processed = await renew_if_due(async_db_session, now=datetime(2026, 2, 5, 0, 0, tzinfo=UTC))
    await async_db_session.commit()
    await async_db_session.refresh(sub)
    await async_db_session.refresh(wallet)

    assert processed == 1
    assert sub.status == "past_due"
    assert sub.period_end == period_end
    assert wallet.balance == Decimal("10.00")
