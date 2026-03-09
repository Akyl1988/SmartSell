from __future__ import annotations

from datetime import datetime

from app.schemas.base import BaseSchema


class TenantDiagnosticsBilling(BaseSchema):
    state: str | None = None
    grace_until: datetime | None = None
    last_payment_status: str | None = None


class TenantDiagnosticsKaspi(BaseSchema):
    connected: bool
    last_successful_sync_at: datetime | None = None
    last_failed_sync_at: datetime | None = None
    last_error_summary: str | None = None
    token_or_session_health: str | None = None
    last_import_status: str | None = None
    last_export_status: str | None = None


class TenantDiagnosticsRepricing(BaseSchema):
    enabled: bool
    last_run_at: datetime | None = None
    last_status: str | None = None


class TenantDiagnosticsInventory(BaseSchema):
    reservations_enabled: bool
    last_inventory_issue_at: datetime | None = None


class TenantDiagnosticsSupport(BaseSchema):
    last_request_id: str | None = None
    open_incident_flag: bool
    notes_for_support: str | None = None


class TenantDiagnosticsSummaryOut(BaseSchema):
    company_id: int
    company_name: str
    plan: str | None = None
    subscription_state: str | None = None
    lifecycle_state: str | None = None
    lifecycle_reason: str | None = None
    lifecycle_source: str | None = None
    billing: TenantDiagnosticsBilling
    kaspi: TenantDiagnosticsKaspi
    repricing: TenantDiagnosticsRepricing
    inventory: TenantDiagnosticsInventory
    support: TenantDiagnosticsSupport
