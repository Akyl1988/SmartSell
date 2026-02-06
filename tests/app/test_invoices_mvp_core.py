from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.billing import Invoice, Subscription, WalletBalance, WalletTransaction
from app.models.company import Company


@pytest.mark.asyncio
async def test_invoices_unauth_returns_401(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/invoices")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invoices_validation_errors(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
):
    resp = await async_client.post(
        "/api/v1/invoices",
        headers=company_a_admin_headers,
        json={"amount": "0.00", "currency": "KZT"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invoices_unique_conflict_returns_409(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
    monkeypatch,
):
    async def _fixed_number(*args, **kwargs):  # noqa: ANN001, D401
        return "INV-TEST-00001"

    monkeypatch.setattr(Invoice, "generate_number_async", _fixed_number)

    first = await async_client.post(
        "/api/v1/invoices",
        headers=company_a_admin_headers,
        json={"amount": "10.00", "currency": "KZT"},
    )
    assert first.status_code == 201, first.text

    second = await async_client.post(
        "/api/v1/invoices",
        headers=company_a_admin_headers,
        json={"amount": "12.00", "currency": "KZT"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_invoices_tenant_isolation(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers, company_b_admin_headers
):
    created = await async_client.post(
        "/api/v1/invoices",
        headers=company_a_admin_headers,
        json={"amount": "15.00", "currency": "KZT"},
    )
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    other = await async_client.get(f"/api/v1/invoices/{invoice_id}", headers=company_b_admin_headers)
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_invoice_status_transitions(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
):
    created = await async_client.post(
        "/api/v1/invoices",
        headers=company_a_admin_headers,
        json={"amount": "20.00", "currency": "KZT"},
    )
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    issued = await async_client.post(
        f"/api/v1/invoices/{invoice_id}/issue",
        headers=company_a_admin_headers,
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "issued"
    assert issued.json()["issued_at"]

    update_after_issue = await async_client.put(
        f"/api/v1/invoices/{invoice_id}",
        headers=company_a_admin_headers,
        json={"description": "new"},
    )
    assert update_after_issue.status_code == 409


@pytest.mark.asyncio
async def test_invoice_pay_idempotent(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/invoices",
        headers=company_a_admin_headers,
        json={"amount": "25.00", "currency": "KZT"},
    )
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    issue = await async_client.post(
        f"/api/v1/invoices/{invoice_id}/issue",
        headers=company_a_admin_headers,
    )
    assert issue.status_code == 200, issue.text

    wallet = WalletBalance(company_id=1001, balance=Decimal("100.00"), currency="KZT")
    async_db_session.add(wallet)
    await async_db_session.commit()

    headers = {**company_a_admin_headers, "X-Request-Id": "req-123"}
    first = await async_client.post(f"/api/v1/invoices/{invoice_id}/pay", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "paid"

    second = await async_client.post(f"/api/v1/invoices/{invoice_id}/pay", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "paid"

    rows = (
        (
            await async_db_session.execute(
                select(WalletTransaction).where(WalletTransaction.client_request_id == "req-123")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.no_subscription
async def test_invoices_subscription_inactive_blocked(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
):
    now = datetime.now(UTC)
    company = (await async_db_session.execute(select(Company).where(Company.id == 1001))).scalars().first()
    if company is None:
        company = Company(id=1001, name="Company 1001")
        async_db_session.add(company)
        await async_db_session.flush()

    sub = Subscription(
        company_id=company.id,
        plan=normalize_plan_id("start") or "trial",
        status="canceled",
        billing_cycle="monthly",
        price=Decimal("0.00"),
        currency="KZT",
        started_at=now,
        period_start=now,
        period_end=now + timedelta(days=30),
        next_billing_date=now + timedelta(days=31),
    )
    async_db_session.add(sub)
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/invoices", headers=company_a_admin_headers)
    assert resp.status_code == 402
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"
