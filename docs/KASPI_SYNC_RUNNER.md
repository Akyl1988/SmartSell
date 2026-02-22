# Kaspi Orders Sync Runner - Implementation Summary

## Overview

Production-safe periodic background task that runs Kaspi orders sync for all active companies.

## Features

✅ **Startup Hook Guard**: Only starts when `ENABLE_KASPI_SYNC_RUNNER=1` and NOT in testing mode  
✅ **Multi-Company Support**: Iterates all active companies (Company.is_active=True)  
✅ **Failure Isolation**: One company failing doesn't stop others (asyncio.gather with return_exceptions=True)  
✅ **Concurrency Control**: Semaphore limits max concurrent syncs (default: 3)  
✅ **Jitter**: Random delay between syncs to prevent thundering herd  
✅ **Structured Logging**: Detailed logs for monitoring and debugging  
✅ **Graceful Shutdown**: Task is cancelled on application shutdown  

## Catalog Strategy Note

Kaspi Shop API does not provide a full catalog pull via X-Auth-Token. Use the products import status endpoints instead:

- `GET /api/v1/kaspi/products/import?i=<import_code>`
- `GET /api/v1/kaspi/products/import/result?i=<import_code>`

The legacy `POST /api/v1/kaspi/products/sync` is retained for compatibility and returns
`catalog_pull_not_supported`.

## Local Import Cycle

Minimal production-safe flow for offers dataset + goods import:

> Warning: The products import endpoint uses the Kaspi attributes schema and does NOT update prices or stock levels.
> Use feed uploads or a future dedicated price/stock import when available.

1) Build offers dataset:
    - `POST /api/v1/kaspi/offers/rebuild`
    - Or manual upload: `POST /api/v1/kaspi/offers/import` (CSV/JSON file)
2) Start import run:
    - `POST /api/v1/kaspi/products/import/start`
3) Upload offers payload to Kaspi:
    - `POST /api/v1/kaspi/products/import/upload?i=<import_code>`
4) Check status/result:
    - `GET /api/v1/kaspi/products/import?i=<kaspi_import_code>`
    - `GET /api/v1/kaspi/products/import/result?i=<kaspi_import_code>`
5) Sync now uses offers if present:
    - `POST /api/v1/kaspi/sync/now`

## Operator Runbook (Goods Import)

1) Build offers dataset:
    - `POST /api/v1/kaspi/offers/rebuild`
    - Or upload: `POST /api/v1/kaspi/offers/import` (CSV/JSON file)
2) Start a goods import run:
    - `POST /api/v1/kaspi/products/import/start`
3) Upload offers payload to Kaspi (idempotent within 24h by payload hash):
    - `POST /api/v1/kaspi/products/import/upload?i=<import_code>`
4) Polling runner finalizes the run:
    - Enable `KASPI_IMPORT_POLL_ENABLED=1`
    - Runner polls status/result with backoff until terminal status
5) Sync now status expectations:
    - Returns `ok` only when goods import is `success_applied`
    - Returns `partial` with `import_pending`, `import_failed`, or `import_noop` otherwise

## Configuration

Environment variables:

- `ENABLE_KASPI_SYNC_RUNNER`: Set to `1` to enable (default: `0`)
- `KASPI_SYNC_INTERVAL_SECONDS`: Interval between sync runs (default: `300` = 5 minutes)

### Kaspi Goods Import Poller (APScheduler)

Background polling for `/api/v1/kaspi/products/import/*` runs with backoff and advisory lock.

Environment variables:

- `KASPI_IMPORT_POLL_ENABLED`: Set to `1` to enable (default: `0`)
- `KASPI_IMPORT_POLL_INTERVAL_SECONDS`: Polling interval (default: `60`)
- `KASPI_IMPORT_POLL_MAX_CONCURRENCY`: Parallel polls per tick (default: `5`)
- `KASPI_IMPORT_POLL_BATCH_SIZE`: Max runs per tick (default: `100`)
- `KASPI_IMPORT_POLL_BACKOFF_BASE_SECONDS`: Base backoff (default: `30`)
- `KASPI_IMPORT_POLL_BACKOFF_MAX_SECONDS`: Max backoff (default: `900`)

## Implementation

### Module: `app/services/kaspi_orders_sync_runner.py`

Main function: `run_kaspi_orders_sync_once()`

**Parameters:**
- `max_concurrent`: Maximum concurrent syncs (default: 3)
- `base_delay_seconds`: Base jitter delay (default: 1.0s)
- `max_delay_seconds`: Maximum jitter delay (default: 60.0s)

**Returns:**
```python
{
    "success": int,  # Successfully synced companies
    "failed": int,   # Failed syncs (exceptions)
    "locked": int,   # Skipped (already running)
    "total": int     # Total companies attempted
}
```

### Integration: `app/main.py`

Startup hook in `lifespan()` context manager:

```python
if not disable_hooks and enable_kaspi_sync and not _GLOBAL.get("kaspi_sync_started"):
    from app.services.kaspi_orders_sync_runner import run_kaspi_orders_sync_once

    async def _kaspi_sync_loop():
        interval_seconds = int(os.getenv("KASPI_SYNC_INTERVAL_SECONDS", "300"))
        logger.info("kaspi_sync_runner: background task started", interval_seconds=interval_seconds)
        while True:
            try:
                await run_kaspi_orders_sync_once()
            except Exception as exc:
                logger.error("kaspi_sync_runner: unexpected error in loop", error=str(exc), exc_info=True)
            await asyncio.sleep(interval_seconds)

    kaspi_sync_task = asyncio.create_task(_kaspi_sync_loop())
    _GLOBAL["kaspi_sync_task"] = kaspi_sync_task
    _GLOBAL["kaspi_sync_started"] = True
```

Shutdown hook cancels task gracefully:

```python
if kaspi_sync_task and not kaspi_sync_task.done():
    kaspi_sync_task.cancel()
    try:
        await kaspi_sync_task
    except asyncio.CancelledError:
        pass
    logger.info("Kaspi sync runner stopped")
```

## Exception Handling

- **`KaspiSyncAlreadyRunning`**: Logged as info, counted as "locked", continues to next company
- **`asyncio.TimeoutError`**: Logged as warning, counted as "failed", continues to next company
- **Generic `Exception`**: Logged as error with traceback, counted as "failed", continues to next company

## Tests

File: `tests/test_kaspi_orders_sync_runner.py`

**Test Coverage:**
1. ✅ `test_runner_not_started_in_testing_mode` - Verifies `should_disable_startup_hooks()` blocks startup in tests
2. ✅ `test_runner_iterates_multiple_companies_with_isolation` - Multi-company with one failure
3. ✅ `test_runner_handles_locked_sync` - KaspiSyncAlreadyRunning handling
4. ✅ `test_runner_handles_timeout` - asyncio.TimeoutError handling
5. ✅ `test_runner_no_companies_returns_empty_summary` - Empty company list
6. ✅ `test_runner_respects_max_concurrent` - Semaphore concurrency limit

**Test Results:**
```
6 passed in 21.98s
```

## Usage

### Development

```bash
# Enable runner with 2-minute interval
export ENABLE_KASPI_SYNC_RUNNER=1
export KASPI_SYNC_INTERVAL_SECONDS=120
uvicorn app.main:app --reload
```

### Production

```bash
# Enable runner with 5-minute interval (default)
ENABLE_KASPI_SYNC_RUNNER=1 \
KASPI_SYNC_INTERVAL_SECONDS=300 \
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

### Docker

```dockerfile
ENV ENABLE_KASPI_SYNC_RUNNER=1
ENV KASPI_SYNC_INTERVAL_SECONDS=300
```

## Monitoring

### Logs

Structured logs with `structlog`:

```
2026-01-11 17:14:13 [info] kaspi_sync_runner: starting sync run
2026-01-11 17:14:13 [info] kaspi_sync_runner: found companies count=2
2026-01-11 17:14:13 [info] kaspi_sync_runner: sync success company_id=9002 company_name=Test Company 2 fetched=0 inserted=0 updated=0
2026-01-11 17:14:13 [error] kaspi_sync_runner: sync failed company_id=9001 company_name=Test Company 1 error=...
2026-01-11 17:14:13 [info] kaspi_sync_runner: sync run complete failed=1 locked=0 success=1 total=2
```

### Metrics

Summary dict returned by `run_kaspi_orders_sync_once()` can be used to emit metrics:

- `kaspi_sync_success_count`
- `kaspi_sync_failed_count`
- `kaspi_sync_locked_count`
- `kaspi_sync_duration_seconds`

## Safety Guarantees

1. **No Auto-Start in Testing**: `should_disable_startup_hooks()` check prevents startup during pytest
2. **Per-Company Sessions**: Each company gets separate AsyncSession to avoid transaction conflicts
3. **Advisory Lock Protection**: Existing `KaspiService.sync_orders` uses PostgreSQL advisory lock (per-company)
4. **Non-Blocking**: Uses `pg_try_advisory_xact_lock` - skips if already locked, doesn't block
5. **Graceful Shutdown**: Task cancellation with proper cleanup in lifespan finally block

## Related Files

- **Implementation**: [app/services/kaspi_orders_sync_runner.py](../app/services/kaspi_orders_sync_runner.py)
- **Integration**: [app/main.py](../app/main.py) (lines 836-862)
- **Tests**: [tests/test_kaspi_orders_sync_runner.py](../tests/test_kaspi_orders_sync_runner.py)
- **Sync Service**: [app/services/kaspi_service.py](../app/services/kaspi_service.py) (`sync_orders` method)

## Changelog

### 2026-01-11
- ✅ Initial implementation
- ✅ Startup integration with environment variable guards
- ✅ Comprehensive test suite (6 tests)
- ✅ All tests passing (6/6 runner tests, 25/25 existing sync tests)
- ✅ Ruff formatting and linting passed
