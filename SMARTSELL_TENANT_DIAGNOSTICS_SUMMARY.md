# SMARTSELL_TENANT_DIAGNOSTICS_SUMMARY

## Purpose
Tenant Diagnostics Summary is the minimum support-facing view that explains the current operational state of a tenant without requiring raw-code investigation.

## Goals
- reduce founder-only debugging
- reduce support response time
- make tenant state explainable
- surface integration and billing problems quickly
- provide one place to inspect current tenant health

## Operator evidence (2026-03-09)

### Real support-style check
- Endpoint: `GET /api/v1/admin/tenants/1/diagnostics`
- Access model: platform admin (`require_platform_admin`)
- Runtime result: `HTTP 200`

### Raw response example (captured)
```json
{
  "company_id": 1,
  "company_name": "Dev Company",
  "plan": "pro",
  "subscription_state": "active",
  "lifecycle_state": "ACTIVE",
  "lifecycle_reason": "subscription_active",
  "lifecycle_source": "subscriptions.state.is_subscription_active",
  "retention_policy_version": "2026-03-09",
  "retention_limits": {
    "orders_days": 3650,
    "campaigns_days": 730,
    "logs_days": 180,
    "events_days": 365,
    "reports_days": 180,
    "diagnostics_snapshots_days": 90
  },
  "billing": {
    "state": "active",
    "grace_until": null,
    "last_payment_status": null
  },
  "kaspi": {
    "connected": true,
    "last_successful_sync_at": "2026-02-21T04:24:20.557748",
    "last_failed_sync_at": null,
    "last_error_summary": null,
    "token_or_session_health": null,
    "last_import_status": null,
    "last_export_status": "pending"
  },
  "repricing": {
    "enabled": true,
    "last_run_at": "2026-02-28T10:58:14.310212",
    "last_status": "done"
  },
  "inventory": {
    "reservations_enabled": true,
    "last_inventory_issue_at": null
  },
  "support": {
    "last_request_id": "ef168bbb-44c1-4395-8b46-337c1b3273ac",
    "open_incident_flag": false,
    "notes_for_support": null
  }
}
```

### Operator interpretation for Kaspi triage
- Is Kaspi token/config configured? **Yes (integration configured)**: `kaspi.connected=true`.
- When was last orders sync? `kaspi.last_successful_sync_at=2026-02-21T04:24:20.557748`.
- Did last sync succeed? **No recent failure evidence**: `last_failed_sync_at=null`, `last_error_summary=null`.
- Are feeds uploaded? **Upload/generation pipeline has pending state**: `last_export_status=pending`.
- Are catalog/import signals present? **No recent import status in summary**: `last_import_status=null`.
- Session/token health currently exposed as nullable support signal: `token_or_session_health=null`.

### Support troubleshooting steps (no DB access)
1. Call `GET /api/v1/admin/tenants/{company_id}/diagnostics` as platform admin.
2. Check `kaspi.connected`:
   - `false` -> tenant integration not connected/configured; verify tenant setup and Kaspi link path.
   - `true` -> continue to sync health checks.
3. Check sync outcome:
   - `last_failed_sync_at` or `last_error_summary` set -> treat as active sync incident and triage by error summary.
   - both null -> no reported last sync failure.
4. Check freshness:
   - `last_successful_sync_at` stale or null -> run integration diagnostics flow and trigger controlled sync/check.
5. Check feed/import pipeline:
   - `last_export_status` in non-success terminal state or prolonged `pending` -> investigate export/upload job path.
   - `last_import_status` null/failed -> investigate import stage and tenant catalog intake.
6. Use `support.last_request_id` for correlated logs/tickets and incident notes.

### Verification outcome
- Existing diagnostics output is sufficient for first-line Kaspi support triage without direct DB access.
- Operators can answer core integration questions from one endpoint response and decide next troubleshooting step.

## Consolidated runtime usage evidence (2026-03-09)

Diagnostics summary usage is now evidenced repeatedly together with support triage preview in operator incident cycles documented in `SMARTSELL_INCIDENT_PROCESS.md`:
- Cycle #1: `GET /api/v1/admin/tenants/1/diagnostics` -> `200`, `POST /api/v1/admin/tenants/1/support-triage-preview` -> `200`
- Cycle #2: `CYCLEA_DIAGNOSTICS_HTTP=200`, `CYCLEA_TRIAGE_HTTP=200`
- Cycle #3: `CYCLEB_DIAGNOSTICS_HTTP=200`, `CYCLEB_TRIAGE_HTTP=200`
- Cycle #4: `TENANT_DIAGNOSTICS_HTTP=200`, `SUPPORT_TRIAGE_HTTP=200`

What this confirms:
- endpoint contract is operational in repeated runtime checks;
- support can repeatedly retrieve integration/billing/request-context signals from diagnostics and classify next actions via triage preview;
- diagnostics + triage flow is reusable without DB access.

Honest remaining gap to `Exists` for this row:
- repeated evidence is currently simulation-style/operator-run; still missing repeated customer-originated real support cases explicitly recorded end-to-end.

## Minimum diagnostics fields

### Tenant identity
- company_id
- company_name
- plan
- subscription_state

### Billing
- billing_state
- grace_until
- last_payment_status

### Kaspi
- kaspi_connected
- last_successful_sync_at
- last_failed_sync_at
- last_error_summary

### Repricing
- repricing_enabled
- last_repricing_run_at
- last_repricing_status

### Preorders / Inventory
- reservations_enabled
- last_inventory_issue_at

### System / Support
- last_request_id
- open_incident_flag
- notes_for_support

## First suggested response shape

```json
{
  "company_id": 123,
  "company_name": "Demo Store",
  "plan": "pro",
  "subscription_state": "active",
  "billing": {
    "state": "active",
    "grace_until": null,
    "last_payment_status": "paid"
  },
  "kaspi": {
    "connected": true,
    "last_successful_sync_at": "2026-03-08T10:00:00Z",
    "last_failed_sync_at": null,
    "last_error_summary": null
  },
  "repricing": {
    "enabled": true,
    "last_run_at": "2026-03-08T09:30:00Z",
    "last_status": "ok"
  },
  "inventory": {
    "reservations_enabled": true,
    "last_inventory_issue_at": null
  },
  "support": {
    "last_request_id": "req_123",
    "open_incident_flag": false,
    "notes_for_support": null
  }
}