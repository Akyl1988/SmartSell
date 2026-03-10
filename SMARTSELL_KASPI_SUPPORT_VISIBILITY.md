# SMARTSELL_KASPI_SUPPORT_VISIBILITY

## 1. Purpose
Define the minimum Kaspi support visibility contract for first-client operations so support can quickly answer: “Is this tenant connected, syncing, or failing?”

## 2. What support must see for each tenant
- Current Kaspi connection state for the tenant.
- Last known successful sync signal.
- Last known failed sync signal and short error summary.
- Token/session health indicator when available.
- Last import/export processing status when available.
- Enough context to decide whether to retry, escalate, or open incident.

## 3. Minimum Kaspi visibility fields
- `company_id`
- `kaspi_connected`
- `last_successful_sync_at`
- `last_failed_sync_at`
- `last_error_summary`
- `token_or_session_health` (if available)
- `last_import_status` (if available)
- `last_export_status` (if available)

Notes:
- If a field has no reliable source yet, return `null`/`unknown` and mark as pending.

## 4. Recommended data sources in current codebase
Use existing persisted data first; avoid adding new schema for this contract phase.

- Tenant-level diagnostics source:
  - `GET /api/v1/admin/tenants/{company_id}/diagnostics`
  - Existing Kaspi-related fields already exposed there: connection + sync/error summary.
- Sync state:
  - `app/models/kaspi_order_sync_state.py` (`last_synced_at`, `last_error_at`, `last_error_message`, `last_error_code`).
- Session health (if present):
  - `app/models/kaspi_mc_session.py` (`is_active`, `last_used_at`, `last_error`).
- Import/export status (if present):
  - `app/models/kaspi_goods_import.py` (status/error fields).
  - `app/models/kaspi_feed_export.py` and `app/models/kaspi_feed_upload.py` (status/error/attempt timestamps).

Current coverage status:
- Core connection + sync failure visibility: partial.
- Session health and last import/export status: available in models, but may be only partially surfaced in support-facing API.

## 5. Support actions enabled by this visibility
- Confirm if a tenant is connected vs disconnected before troubleshooting.
- Distinguish “never synced” vs “synced before, now failing.”
- Identify likely failure class quickly (auth/session/token vs data/import/export).
- Decide immediate next action:
  - retry safe operation,
  - request tenant credential/session refresh,
  - escalate to engineering,
  - open/attach incident record.

## 6. Failure examples
- Kaspi sync fails repeatedly with recent `last_failed_sync_at` and error summary.
- Tenant appears connected but session health indicates stale/invalid session.
- Import status shows failed; export/upload status remains pending or failed.
- Orders are stale while last successful sync is old and no recent success signal.

## 7. Evidence required to move from Partial to Exists
- Support-facing endpoint/view returns required minimum fields for at least one real tenant.
- At least one automated test validates field contract and error-state visibility.
- At least one support runbook example references this visibility contract in a real triage flow.
- Evidence links recorded (API payload sample, test path, and triage note).
