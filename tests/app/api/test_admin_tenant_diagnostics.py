from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models.audit_log import AuditLog
from app.models.billing import BillingPayment, Subscription
from app.models.company import Company
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.repricing import RepricingRule, RepricingRun

pytestmark = pytest.mark.asyncio


async def test_admin_tenant_diagnostics_summary(async_client, async_db_session, auth_headers):
    company = Company(id=9501, name="Diag Store", subscription_plan="pro", kaspi_store_id="kaspi-1")
    async_db_session.add(company)
    await async_db_session.flush()

    payment = BillingPayment(
        company_id=company.id,
        amount=Decimal("100.00"),
        currency="KZT",
        status="captured",
        method="card",
    )
    async_db_session.add(payment)
    await async_db_session.flush()

    subscription = Subscription(
        company_id=company.id,
        plan="pro",
        status="active",
        billing_cycle="monthly",
        price=Decimal("0.00"),
        currency="KZT",
        period_start=datetime.utcnow(),
        period_end=datetime.utcnow() + timedelta(days=30),
        grace_until=datetime.utcnow() + timedelta(days=7),
        last_payment_id=payment.id,
    )
    async_db_session.add(subscription)

    sync_state = KaspiOrderSyncState(
        company_id=company.id,
        last_synced_at=datetime.utcnow(),
        last_error_at=datetime.utcnow(),
        last_error_message="kaspi error",
    )
    async_db_session.add(sync_state)

    rule = RepricingRule(company_id=company.id, name="rule", enabled=True, is_active=True)
    async_db_session.add(rule)
    await async_db_session.flush()

    run = RepricingRun(company_id=company.id, rule_id=rule.id, status="completed", started_at=datetime.utcnow())
    async_db_session.add(run)

    audit = AuditLog(action="diagnostics_check", company_id=company.id, request_id="req_123")
    async_db_session.add(audit)

    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/diagnostics",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload.get("company_id") == company.id
    assert payload.get("company_name") == company.name
    assert payload.get("subscription_state") == "active"
    assert payload.get("billing", {}).get("state") == "active"
    assert payload.get("billing", {}).get("last_payment_status") == "captured"
    assert payload.get("kaspi", {}).get("connected") is True
    assert payload.get("kaspi", {}).get("last_error_summary") == "kaspi error"
    assert payload.get("repricing", {}).get("enabled") is True
    assert payload.get("repricing", {}).get("last_status") == "completed"
    assert payload.get("inventory", {}).get("reservations_enabled") is True
    assert payload.get("support", {}).get("last_request_id") == "req_123"


async def test_admin_tenant_diagnostics_forbidden(async_client, async_db_session, company_a_admin_headers):
    company = Company(id=9502, name="Diag Store 2", subscription_plan="start")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/tenants/{company.id}/diagnostics",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")
