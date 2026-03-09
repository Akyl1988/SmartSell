from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.subscriptions.features import FEATURE_PREORDERS, FEATURE_REPRICING
from app.core.subscriptions.plan_catalog import get_plan_features, normalize_plan_id
from app.core.subscriptions.state import get_company_subscription
from app.models.audit_log import AuditLog
from app.models.billing import BillingPayment
from app.models.company import Company
from app.models.integration_event import IntegrationEvent
from app.models.kaspi_mc_session import KaspiMcSession
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.repricing import RepricingRule, RepricingRun
from app.schemas.tenant_diagnostics import (
    TenantDiagnosticsBilling,
    TenantDiagnosticsInventory,
    TenantDiagnosticsKaspi,
    TenantDiagnosticsRepricing,
    TenantDiagnosticsSummaryOut,
    TenantDiagnosticsSupport,
)

_BILLING_STATE_MAP = {
    "trial": "trial",
    "active": "active",
    "overdue": "grace",
    "paused": "suspended",
    "canceled": "cancelled",
    "expired": "cancelled",
}


def _map_billing_state(subscription_state: str | None) -> str | None:
    if not subscription_state:
        return None
    return _BILLING_STATE_MAP.get(subscription_state, subscription_state)


def _normalize_plan(plan: str | None) -> str | None:
    return normalize_plan_id(plan, default=plan or None) if plan else None


def _pick_latest_request_id(
    audit_row: tuple[str | None, datetime] | None,
    event_row: tuple[str | None, datetime] | None,
) -> str | None:
    if audit_row and event_row:
        _, audit_at = audit_row
        _, event_at = event_row
        if event_at and audit_at and event_at > audit_at:
            return event_row[0]
        return audit_row[0]
    if audit_row:
        return audit_row[0]
    if event_row:
        return event_row[0]
    return None


async def get_tenant_diagnostics_summary(
    db: AsyncSession,
    *,
    company_id: int,
) -> TenantDiagnosticsSummaryOut:
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    subscription = await get_company_subscription(db, company_id)
    plan = (subscription.plan if subscription else None) or company.subscription_plan
    subscription_state = subscription.effective_status if subscription else None
    billing_state = _map_billing_state(subscription_state)

    grace_until = None
    if subscription is not None:
        grace_until = subscription.grace_until or subscription.grace_expires_at()

    payment = None
    if subscription and subscription.last_payment_id:
        payment = await db.get(BillingPayment, subscription.last_payment_id)
    if payment is None:
        stmt = (
            select(BillingPayment)
            .where(BillingPayment.company_id == company_id)
            .order_by(BillingPayment.created_at.desc())
            .limit(1)
        )
        payment = (await db.execute(stmt)).scalar_one_or_none()

    sync_state = (
        await db.execute(select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id).limit(1))
    ).scalar_one_or_none()

    kaspi_session_exists = (
        await db.execute(
            select(KaspiMcSession.id)
            .where(KaspiMcSession.company_id == company_id, KaspiMcSession.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()

    kaspi_connected = bool(company.kaspi_store_id) or kaspi_session_exists is not None
    last_error_summary = None
    if sync_state is not None:
        last_error_summary = sync_state.last_error_message or sync_state.last_error_code

    repricing_rule_exists = (
        await db.execute(
            select(RepricingRule.id)
            .where(
                RepricingRule.company_id == company_id,
                RepricingRule.enabled.is_(True),
                RepricingRule.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    repricing_run = (
        await db.execute(
            select(RepricingRun)
            .where(RepricingRun.company_id == company_id)
            .order_by(RepricingRun.started_at.is_(None))
            .order_by(desc(RepricingRun.started_at))
            .order_by(desc(RepricingRun.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    plan_features = get_plan_features(_normalize_plan(plan) or "start")
    repricing_enabled = bool(repricing_rule_exists) and FEATURE_REPRICING in plan_features
    reservations_enabled = FEATURE_PREORDERS in plan_features

    audit_row = (
        await db.execute(
            select(AuditLog.request_id, AuditLog.created_at)
            .where(AuditLog.company_id == company_id, AuditLog.request_id.is_not(None))
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).first()

    event_row = (
        await db.execute(
            select(IntegrationEvent.request_id, IntegrationEvent.occurred_at)
            .where(IntegrationEvent.company_id == company_id, IntegrationEvent.request_id.is_not(None))
            .order_by(IntegrationEvent.occurred_at.desc())
            .limit(1)
        )
    ).first()

    last_request_id = _pick_latest_request_id(audit_row, event_row)

    return TenantDiagnosticsSummaryOut(
        company_id=company.id,
        company_name=company.name,
        plan=plan,
        subscription_state=subscription_state,
        billing=TenantDiagnosticsBilling(
            state=billing_state,
            grace_until=grace_until,
            last_payment_status=payment.status if payment else None,
        ),
        kaspi=TenantDiagnosticsKaspi(
            connected=kaspi_connected,
            last_successful_sync_at=sync_state.last_synced_at if sync_state else None,
            last_failed_sync_at=sync_state.last_error_at if sync_state else None,
            last_error_summary=last_error_summary,
        ),
        repricing=TenantDiagnosticsRepricing(
            enabled=repricing_enabled,
            last_run_at=(repricing_run.started_at or repricing_run.created_at) if repricing_run else None,
            last_status=repricing_run.status if repricing_run else None,
        ),
        inventory=TenantDiagnosticsInventory(
            reservations_enabled=reservations_enabled,
            last_inventory_issue_at=None,
        ),
        support=TenantDiagnosticsSupport(
            last_request_id=last_request_id,
            open_incident_flag=False,
            notes_for_support=None,
        ),
    )
