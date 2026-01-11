# Timeout Hardening for CI Stability

## Overview
This document describes the timeout mechanisms implemented to prevent CI hangs during Kaspi orders sync and other long-running operations.

## Changes

### 1. Kaspi API Hard Timeout
**File:** [app/services/kaspi_service.py](../app/services/kaspi_service.py#L695-L710)

The `_fetch_orders_page` method now wraps `self.get_orders()` in `asyncio.wait_for()` with a configurable timeout:

```python
fetch_timeout = float(getattr(settings, "KASPI_HTTP_TIMEOUT_SEC", 60))
return await asyncio.wait_for(
    self.get_orders(...),
    timeout=fetch_timeout,
)
```

**Exception Handling:**
- `asyncio.TimeoutError`: Hard timeout from `asyncio.wait_for()` (always treated as transient)
- `httpx.TimeoutException`: Network-level timeout from httpx client
- Both trigger retry logic with exponential backoff (3 attempts)

**Default:** 60 seconds (configurable via `KASPI_HTTP_TIMEOUT_SEC` environment variable)

This ensures that even if Kaspi's API becomes unresponsive, the sync operation will fail fast rather than hanging indefinitely.

### 2. pytest-timeout Plugin
**File:** [requirements.txt](../requirements.txt)

Added `pytest-timeout==2.3.1` to test dependencies. This plugin:
- Enforces global timeout for all tests (default: 600s / 10 minutes)
- Prevents individual tests from hanging CI jobs
- Uses thread-based timeout method for better compatibility with async code

### 3. Global pytest Timeout Configuration
**File:** [pytest.ini](../pytest.ini)

```ini
[pytest]
timeout = 600
timeout_method = thread
```

**Rationale:**
- 600s (10 minutes) global timeout prevents any single test from hanging indefinitely
- `thread` method works reliably with asyncio and pytest-asyncio in strict mode
- Can be overridden per-test with `@pytest.mark.timeout(seconds)`

## Testing
All Kaspi sync timeout tests pass:
```bash
pytest -q tests/app/api/test_kaspi_orders_sync.py::test_sync_timeout_records_error
pytest -q tests/app/api/test_kaspi_orders_sync.py::test_sync_timeout_maps_to_504
```

## CI Impact
- **Before:** Kaspi sync could hang indefinitely, causing CI jobs to timeout after ~30 minutes
- **After:** 
  - Individual API calls fail after 60s
  - Individual tests fail after 600s
  - Clear error messages in logs for debugging

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KASPI_HTTP_TIMEOUT_SEC` | 60 | Hard timeout for Kaspi API calls (seconds) |

## Future Improvements
- Consider reducing `KASPI_HTTP_TIMEOUT_SEC` to 30s for faster failure detection
- Add per-endpoint timeout configuration if different APIs have different SLAs
- Implement circuit breaker pattern for repeated timeout failures
