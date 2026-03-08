# SMARTSELL_TENANT_DIAGNOSTICS_SUMMARY

## Purpose
Tenant Diagnostics Summary is the minimum support-facing view that explains the current operational state of a tenant without requiring raw-code investigation.

## Goals
- reduce founder-only debugging
- reduce support response time
- make tenant state explainable
- surface integration and billing problems quickly
- provide one place to inspect current tenant health

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