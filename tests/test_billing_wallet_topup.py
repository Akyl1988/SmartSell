from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.core.security import create_access_token, get_password_hash
from app.core.subscriptions import plan_catalog
from app.models.billing import Subscription, WalletBalance, WalletTransaction
from app.models.company import Company
from app.models.user import User
from app.services.subscriptions import activate_plan, renew_if_due

pytestmark = pytest.mark.asyncio


def _ceil_midnight(dt: datetime) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


def _add_months_anchor(dt: datetime, anchor_day: int, months: int) -> datetime:
    month_index = (dt.month - 1) + months
    year = dt.year + (month_index // 12)
    month = (month_index % 12) + 1
    last_day = monthrange(year, month)[1]
    day = min(max(anchor_day, 1), last_day)
    return dt.replace(year=year, month=month, day=day)


def _superuser_headers_without_company() -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990022").first()
        if not user:
            user = User(
                phone="+79999990022",
                company_id=None,
                hashed_password=get_password_hash("Secret123!"),
                role="admin",
                is_superuser=True,
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = None
            user.role = "admin"
            user.is_superuser = True
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


async def _ensure_company(async_db_session, company_id: int, *, plan: str = "start") -> Company:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()
    company.subscription_plan = plan
    await async_db_session.commit()
    return company


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


async def test_manual_topup_denies_store_admin(async_client, async_db_session, company_a_admin_headers):
    company = Company(id=9011, name="Topup Denied Co")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/admin/wallet/topup",
        headers=company_a_admin_headers,
        json={
            "companyId": company.id,
            "amount": "10.00",
            "currency": "KZT",
            "external_reference": "topup-deny-001",
            "comment": "store admin",
        },
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"


async def test_manual_topup_allows_superuser(async_client, async_db_session, test_db):
    _ = test_db
    headers = _superuser_headers_without_company()
    company = Company(id=9012, name="Topup Superuser Co")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/admin/wallet/topup",
        headers=headers,
        json={
            "companyId": company.id,
            "amount": "25.00",
            "currency": "KZT",
            "external_reference": "topup-su-001",
            "comment": "superuser credit",
        },
    )
    assert resp.status_code == 200, resp.text
    wallet = await WalletBalance.get_for_company_async(async_db_session, company.id)
    await async_db_session.refresh(wallet)
    assert wallet.balance == Decimal("25.00")


async def test_manual_topup_idempotent(async_client, async_db_session, auth_headers):
    company = Company(id=9002, name="Topup Idempotent Co")
    async_db_session.add(company)
    await async_db_session.commit()

    payload = {
        "companyId": company.id,
        "amount": "100.00",
        "currency": "KZT",
        "external_reference": "topup-002",
        "comment": "manual credit",
    }

    resp1 = await async_client.post("/api/v1/admin/wallet/topup", headers=auth_headers, json=payload)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()

    resp2 = await async_client.post("/api/v1/admin/wallet/topup", headers=auth_headers, json=payload)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()

    assert data2["transaction_id"] == data1["transaction_id"]
    assert Decimal(str(data2["balance"])) == Decimal(str(data1["balance"]))
    assert Decimal(str(data2["balance"])) == Decimal("100")

    wallet = (
        await async_db_session.execute(sa.select(WalletBalance).where(WalletBalance.company_id == company.id))
    ).scalar_one()
    assert wallet.balance == Decimal("100")


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

    company = Company(id=9003, name="Billing Co")
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
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


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

    company = Company(id=9004, name="Grace Renew Co")
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
    assert wallet.balance == Decimal("70")


async def test_anchor_day_rolls_to_month_end(async_db_session, monkeypatch):
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

    company = Company(id=9005, name="Anchor Co")
    async_db_session.add(company)
    await async_db_session.flush()

    wallet = WalletBalance(company_id=company.id, balance=Decimal("100.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.flush()

    period_end = datetime(2026, 1, 31, 0, 0, tzinfo=UTC)
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
        billing_anchor_day=31,
        grace_until=None,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    processed = await renew_if_due(async_db_session, now=datetime(2026, 2, 1, 0, 0, tzinfo=UTC))
    assert processed == 1

    await async_db_session.commit()
    await async_db_session.refresh(sub)

    assert sub.period_end == datetime(2026, 2, 28, 0, 0, tzinfo=UTC)


async def test_renewal_marks_past_due_sets_grace(async_db_session, monkeypatch):
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

    company = Company(id=9006, name="Past Due Co")
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
        billing_anchor_day=period_end.day,
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    processed = await renew_if_due(async_db_session, now=datetime(2026, 2, 5, 0, 0, tzinfo=UTC))
    await async_db_session.commit()
    await async_db_session.refresh(sub)

    assert processed == 1
    assert sub.status == "past_due"
    assert sub.grace_until == _ceil_midnight(period_end + timedelta(days=3))


async def test_admin_trial_then_wallet_renewal(async_client, async_db_session, auth_headers, monkeypatch):
    monkeypatch.setitem(
        plan_catalog.PLAN_CATALOG,
        "pro",
        plan_catalog.PlanCatalogEntry(
            plan_id="pro",
            display_name="Pro",
            price=Decimal("30.00"),
            currency="KZT",
        ),
    )

    company = Company(id=9007, name="Trial Co")
    async_db_session.add(company)
    await async_db_session.commit()

    wallet = WalletBalance(company_id=company.id, balance=Decimal("100.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.commit()

    started_at = datetime.now(UTC)
    resp = await async_client.post(
        "/api/v1/admin/subscriptions/trial",
        headers=auth_headers,
        json={"companyId": company.id, "plan": "pro", "trial_days": 15},
    )
    assert resp.status_code == 200, resp.text

    sub = (
        await async_db_session.execute(sa.select(Subscription).where(Subscription.company_id == company.id))
    ).scalar_one()

    expected_end = started_at + timedelta(days=15)
    assert sub.period_end is not None
    assert abs((sub.period_end - expected_end).total_seconds()) < 5
    assert sub.grace_until == _ceil_midnight(sub.period_end + timedelta(days=3))

    anchor_day = sub.billing_anchor_day or started_at.day
    previous_period_end = sub.period_end
    renew_now = sub.period_end + timedelta(days=1)
    processed = await renew_if_due(async_db_session, now=renew_now)
    await async_db_session.commit()
    await async_db_session.refresh(sub)
    await async_db_session.refresh(wallet)

    assert processed == 1
    assert sub.status == "active"
    assert sub.period_end == _add_months_anchor(previous_period_end, anchor_day, 1)
    assert wallet.balance == Decimal("70.00")
