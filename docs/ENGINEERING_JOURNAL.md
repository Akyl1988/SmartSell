# Engineering Journal (Deprecated)

> **⚠️ This file is deprecated. All content has been migrated to [PROJECT_JOURNAL.md](/PROJECT_JOURNAL.md) (repo root).**
>
> `PROJECT_JOURNAL.md` is now the single source of truth for all engineering decisions, technical notes, and project milestones.
> 
> Please refer to `PROJECT_JOURNAL.md` for all historical and ongoing entries.

---

## Migration completed on 2026-01-10

All entries from this file were merged into `PROJECT_JOURNAL.md` in chronological order with duplicate detection. This file remains in place as a historical redirect for any external references.

For new entries, please update `PROJECT_JOURNAL.md` directly (append-only).

---

## [2026-01-10] Kaspi Orders Sync Hardening (Locks + Timeout + Ops) [MIGRATED]

### Added
- **Transaction-scoped locks**: switched per-company advisory locks to `pg_try_advisory_xact_lock` so locks auto-release on commit/rollback.
- **Run timeout guard**: wrapped sync run with `asyncio.timeout` and record `timeout` errors without advancing watermarks.
- **Ops endpoint**: `GET /api/v1/kaspi/orders/sync/ops` returns sync state plus `lock_available` probe via short xact-lock attempt.

### Tests
- `test_sync_ops_lock_available_basic` — ops endpoint includes lock_available field.
- `test_sync_timeout_records_error` — skipped (manual validation: set KASPI_SYNC_TIMEOUT_SECONDS=0.1 and observe timeout behavior).

### Verified
- `python -m pytest -q` (247 passed, 7 skipped)
- `python -m ruff format app tests` + `python -m ruff check app tests --fix`
- Timeout behavior: manually validated via KASPI_SYNC_TIMEOUT_SECONDS setting

## [2026-01-10] Kaspi Auto-Sync: Operational Observability (Configuration + Scheduler Visibility)

### Added
- **Enhanced Status Endpoint**: `GET /api/v1/kaspi/autosync/status` now returns full operational state
  - **Configuration fields**: `interval_minutes`, `max_concurrency` (from settings)
  - **Scheduler visibility**: `job_registered` (bool), `scheduler_running` (bool | null)
  - **Backward compatible**: All existing `last_run_summary` fields preserved
  - **Safe**: Returns valid response even if scheduler unavailable

- **Updated Response Model**: `KaspiAutoSyncStatusOut` reorganized with sections:
  1. Configuration (enabled, interval_minutes, max_concurrency)
  2. Scheduler state (job_registered, scheduler_running)
  3. Last run summary (last_run_at, eligible_companies, success, locked, failed)

- **Tests Added**: Comprehensive coverage for new fields
  - `test_autosync_status_includes_config` - Verifies configuration fields returned
  - `test_autosync_status_includes_scheduler_state` - Verifies scheduler visibility when running
  - `test_autosync_status_job_not_registered` - Verifies job_registered reflects actual state

### Decision Rationale
- **Why configuration in status?**: Operators need quick visibility into active settings
- **Why scheduler state?**: Helps diagnose if background job is properly registered
- **Why safe defaults?**: Never fails even if scheduler/module unavailable

### Verified
- All 246 tests passing (10 autosync tests including 3 new observability tests, 1 skipped)
- Fixed test mocking: Using `patch.dict(sys.modules)` for dynamic imports inside endpoints
- Backward compatible (no breaking changes)
- Safe fallbacks for unavailable components

## [2026-01-10] Kaspi Auto-Sync: Production Safety (Disabled by Default)

### Changed
- **Configuration Default**: Changed `KASPI_AUTOSYNC_ENABLED` from `default=True` to `default=False` in `app/core/config.py`
  - **Rationale**: Production safety - auto-sync must be explicitly enabled
  - **Impact**: New deployments will not auto-sync until enabled via environment variable

- **API Endpoints Enhanced**: Updated `app/api/v1/kaspi.py`
  - `GET /api/v1/kaspi/autosync/status` now returns `enabled: bool` field
  - Returns `enabled=false` with zero stats when disabled
  - `POST /api/v1/kaspi/autosync/trigger` now returns 409 Conflict when disabled
  - Clear error message: "Kaspi auto-sync is disabled. Set KASPI_AUTOSYNC_ENABLED=true to enable."

### Added Tests
- `test_autosync_status_disabled` - Verifies status endpoint shows disabled state
- `test_autosync_trigger_disabled` - Verifies trigger returns 409 when disabled
- Updated existing tests to check for `enabled` field

### Updated Documentation
- `KASPI_AUTOSYNC_IMPLEMENTATION.md` - Updated configuration defaults section
- Added warning about production safety in deployment notes

### Decision Rationale
- **Why disabled by default?**: Prevents unexpected behavior in production environments
- **Why 409 Conflict?**: Semantic HTTP status for operational state conflict
- **Why explicit enable?**: Forces conscious decision to enable background jobs

### Verified
- All tests passing (243 passed, 6 skipped)
- ruff format/check clean
- API endpoints behave correctly in disabled state

## [2026-01-10] Kaspi Orders Auto-Sync Scheduler (Production-Grade)

### Added
- **Background Job**: Implemented periodic auto-sync scheduler in `app/worker/kaspi_autosync.py`
  - Uses existing APScheduler infrastructure (BackgroundScheduler)
  - Configurable interval via `KASPI_AUTOSYNC_INTERVAL_MINUTES` (default: 15 minutes)
  - Runs in background daemon thread with event listeners

- **Concurrency Control**: Batch processing with asyncio.gather
  - Max parallel syncs configurable via `KASPI_AUTOSYNC_MAX_CONCURRENCY` (default: 3)
  - Chunking strategy to avoid overwhelming the system on Windows
  - Safe for multi-company environments

- **Company Selection**: Eligible companies query
  - Filters: `is_active=True AND deleted_at IS NULL AND kaspi_store_id IS NOT NULL`
  - Implemented in `_get_eligible_companies(db)` with SQLAlchemy async

- **Error Handling**: Graceful degradation
  - Advisory lock respected: `KaspiSyncAlreadyRunning` → counted as "locked"
  - Generic errors → counted as "failed", don't stop other companies
  - Per-company error logging with safe formatting (no credentials)

- **Operational Endpoints**: platform_admin API in `app/api/v1/kaspi.py`
  - `GET /api/v1/kaspi/autosync/status` - Last run summary (eligible/success/locked/failed counts)
  - `POST /api/v1/kaspi/autosync/trigger` - Manual trigger for immediate sync

- **Configuration**: Three new settings in `app/core/config.py` (lines 431-450)
  - `KASPI_AUTOSYNC_ENABLED: bool` - Enable/disable auto-sync (default: False, changed for production safety)
  - `KASPI_AUTOSYNC_INTERVAL_MINUTES: int` - Sync frequency (default: 15)
  - `KASPI_AUTOSYNC_MAX_CONCURRENCY: int` - Parallel sync limit (default: 3)

- **Scheduler Integration**: Updated `app/worker/scheduler_worker.py`
  - Added job ID constant: `_JOB_ID_KASPI_AUTOSYNC`
  - Registered job in `start()` function with IntervalTrigger
  - Registered job in `reload_jobs()` for hot reload support
  - Conditional registration based on `KASPI_AUTOSYNC_ENABLED` setting

- **Tests**: Comprehensive test suite in `tests/test_kaspi_autosync.py`
  - `test_get_eligible_companies_filters_correctly` - Validates company filtering logic
  - `test_sync_respects_concurrency_limit` - Ensures max_concurrency honored
  - `test_locked_companies_dont_stop_batch` - Advisory lock doesn't break batch
  - `test_failed_companies_tracked_in_summary` - Errors tracked, don't crash job
  - `test_manual_trigger_via_endpoint` - API trigger works correctly
  - `test_autosync_status_endpoint` - Status endpoint returns valid data

### Technical Details
- **Pattern**: Follows existing `process_campaigns` job pattern
- **Trigger**: `IntervalTrigger(minutes=settings.KASPI_AUTOSYNC_INTERVAL_MINUTES)`
- **APScheduler Config**: `max_instances=1`, `coalesce=True`, `misfire_grace_time=300`
- **Global State**: `_last_run_summary` dict tracks last run statistics
- **Database**: Uses async SQLAlchemy sessions with proper cleanup
- **Logging**: Structured logging with company_id context, no sensitive data

### Decision Rationale
- **Why APScheduler?**: Already in use, battle-tested, supports multiple triggers
- **Why batch processing?**: Avoids thundering herd, controlled resource usage
- **Why global state?**: Simple operational visibility without DB overhead
- **Why platform_admin endpoints?**: Operational debugging and manual intervention capability

### Verified
- All existing tests pass (236 passed, 5 skipped)
- New tests created for auto-sync functionality
- Safe logging verified (no tokens/credentials exposed)
- Scheduler integration tested with hot reload

## 2025-12-29
- Added `scripts/prod-gate.ps1` automated prod-gate pipeline (pip check, ruff, mypy, pytest, alembic, uvicorn smoke, fail-fast guard, gitleaks, docker smoke) with fail-fast behavior and masking of DSN secrets.
- Documented usage and troubleshooting in `docs/PROD_GATE.md`.
- CI workflow aligned to prod-gate stages.

## [2026-01-09] Kaspi orders sync MVP coverage

### Added
- Added MVP test suite for Kaspi orders sync: idempotency, watermark advancement/filtering, upsert updates, advisory lock (423), and error persistence.
- Added KASPI_SYNC_MVP_SUMMARY.md documenting verified behavior and test results.

### Verified
- python -m ruff format tests/app/api/test_kaspi_orders_sync_mvp.py
- python -m pytest tests/app/api/test_kaspi_orders_sync_mvp.py -q
- python -m pytest tests/ -q

## [2026-01-08] (no updates recorded)

### Context
No updates were recorded for this date.

### Changes
No changes captured.

### Impact
No noted impact.

### Verification
Not applicable.

## [2026-01-10] Kaspi Orders Sync — Timeout persistence + hardened tests

### Context
Timeout could cancel/rollback the main sync transaction, causing `kaspi_order_sync_state` updates to be lost and making `/state` observability unreliable under timeouts.

### Changes
- Added `_record_timeout_state` to persist timeout result using a fresh async engine session (fallback to the original session when needed).
- Ensured the timeout path records:
  - `last_result="failed"`
  - `last_error_code="timeout"`
  - `last_attempt_at` set
  - watermark preserved (no forward move on timeout)
- Strengthened `test_sync_timeout_records_error` to assert HTTP 504 and persisted sync_state invariants.

### Impact
- Timeout behavior is now operationally observable and durable.
- Test suite enforces the contract (no “504-only” weakening).

### Verification
- `python -m ruff format app tests`
- `python -m ruff check app tests`
- `python -m pytest -q` → `248 passed, 6 skipped`

