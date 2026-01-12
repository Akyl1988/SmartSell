# Kaspi Autosync Mutual Exclusion Implementation

**Date**: 2026-01-12  
**Status**: ✅ Complete  
**Tests**: 63 passed, 1 skipped

## Problem

APScheduler `kaspi_autosync` job and main.py `ENABLE_KASPI_SYNC_RUNNER` could run simultaneously, causing:
- Duplicate sync operations
- Potential database contention
- Wasted resources
- Unpredictable behavior

**Root cause**: No coordination between two independent sync mechanisms.

## Solution

Implemented mutual exclusion with runner taking precedence:

### 1. Helper Functions (app/worker/scheduler_worker.py)

```python
def _env_truthy(value: str | None, default: bool = False) -> bool:
    """Check if environment variable is truthy (same logic as main.py)."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")

def should_register_kaspi_autosync() -> bool:
    """
    Determine if Kaspi autosync APScheduler job should be registered.
    
    Returns True only when:
    - settings.KASPI_AUTOSYNC_ENABLED is True
    - AND env ENABLE_KASPI_SYNC_RUNNER is NOT truthy (runner takes precedence)
    """
    runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
    if runner_enabled:
        return False  # Runner takes precedence
    
    scheduler_enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)
    return scheduler_enabled
```

### 2. Scheduler Integration

**Before**:
```python
if getattr(settings, "KASPI_AUTOSYNC_ENABLED", True):  # Unsafe default!
    scheduler.add_job(...)
```

**After**:
```python
if should_register_kaspi_autosync():
    scheduler.add_job(...)
else:
    runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
    if runner_enabled:
        logger.info("Kaspi autosync APScheduler job skipped: runner enabled")
```

### 3. Observability (app/api/v1/kaspi.py)

Extended `KaspiAutoSyncStatusOut` schema:

```python
class KaspiAutoSyncStatusOut(BaseModel):
    # ... existing fields ...
    
    # Mutual exclusion observability
    runner_enabled: bool = Field(False, description="Включен ли main.py runner loop")
    scheduler_job_effective_enabled: bool = Field(
        False, description="Включена ли APScheduler job после mutual exclusion"
    )
```

Status endpoint now shows:
- `runner_enabled`: Is `ENABLE_KASPI_SYNC_RUNNER` active?
- `scheduler_job_effective_enabled`: Will APScheduler job register?

## Decision Matrix

| KASPI_AUTOSYNC_ENABLED | ENABLE_KASPI_SYNC_RUNNER | Scheduler Job Registered | Runner Active | Outcome |
|------------------------|--------------------------|-------------------------|---------------|---------|
| True | 1/true/yes | ❌ NO | ✅ YES | **Runner only** (mutual exclusion) |
| True | 0/false/no | ✅ YES | ❌ NO | **Scheduler only** |
| False | 1/true/yes | ❌ NO | ✅ YES | **Runner only** |
| False | 0/false/no | ❌ NO | ❌ NO | **Neither** (both disabled) |

**Key principle**: Runner always wins when enabled.

## Testing

### test_kaspi_autosync_mutual_exclusion.py (3 tests)

1. **test_env_truthy_helper_logic**: Validates env var parsing
   - Truthy: "1", "true", "TRUE", "yes", "Yes", "on", "ON", "enable", "enabled", "ENABLED"
   - Falsy: "0", "false", "no", "off", "", "random"
   - None: default=False, or explicit default=True

2. **test_mutual_exclusion_logic**: Verifies decision matrix
   - Runner enabled → NO scheduler registration
   - Runner off + autosync enabled → YES scheduler registration
   - Autosync disabled → NO scheduler registration (regardless of runner)

3. **test_mutual_exclusion_observability_in_status_endpoint**: Schema validation
   - Confirms `runner_enabled` and `scheduler_job_effective_enabled` fields exist
   - Validates descriptions mention "runner" and "mutual exclusion"

### Regression Testing

- All 63 existing Kaspi tests: ✅ PASSED
- No breaking changes to existing functionality

## Files Modified

1. **app/worker/scheduler_worker.py**
   - Added `_env_truthy()` helper
   - Added `should_register_kaspi_autosync()` helper
   - Modified `start()` to use helper + log skip
   - Modified `reload_jobs()` to use helper + log skip

2. **app/api/v1/kaspi.py**
   - Extended `KaspiAutoSyncStatusOut` schema (2 new fields)
   - Updated `kaspi_autosync_status()` endpoint to populate new fields

3. **tests/test_kaspi_autosync_mutual_exclusion.py**
   - New file with 3 regression tests

4. **PROJECT_JOURNAL.md**
   - Documented implementation with timestamp

## Configuration

No `.env` changes required. Existing behavior:

- `KASPI_AUTOSYNC_ENABLED=false` (default, safe)
- `ENABLE_KASPI_SYNC_RUNNER=0` (default, off)

To enable **runner-based sync** (recommended for production):
```bash
ENABLE_KASPI_SYNC_RUNNER=1
KASPI_SYNC_INTERVAL_SECONDS=900  # 15 minutes
```

To enable **scheduler-based sync** (legacy/fallback):
```bash
KASPI_AUTOSYNC_ENABLED=true
KASPI_AUTOSYNC_INTERVAL_MINUTES=15
KASPI_AUTOSYNC_MAX_CONCURRENCY=3
```

**DO NOT** enable both simultaneously - mutual exclusion will automatically prefer runner.

## Verification Commands

```bash
# Code quality
ruff format app/worker/scheduler_worker.py app/api/v1/kaspi.py tests/test_kaspi_autosync_mutual_exclusion.py
ruff check app/worker/scheduler_worker.py app/api/v1/kaspi.py tests/test_kaspi_autosync_mutual_exclusion.py

# Tests
pytest tests/test_kaspi_autosync_mutual_exclusion.py -v  # 3 passed
pytest -k "kaspi" -v  # 63 passed, 1 skipped

# Status endpoint (runtime check)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/kaspi/autosync/status
# Expected fields:
# - runner_enabled: true/false
# - scheduler_job_effective_enabled: true/false
```

## Benefits

1. **No duplicate syncs**: Mutual exclusion prevents both mechanisms from running
2. **Predictable behavior**: Clear precedence (runner > scheduler)
3. **Observable**: Status endpoint shows which mechanism is active
4. **Safe defaults**: Both disabled by default, explicit opt-in required
5. **Production-ready**: Logging helps operators understand what's running
6. **Regression-tested**: 3 new tests ensure logic stays correct

## Next Steps

Consider deprecating scheduler-based sync entirely in favor of runner:
- Runner is more flexible (configurable interval)
- Runner is easier to debug (single code path)
- Scheduler adds APScheduler dependency overhead

Deprecation path:
1. Document runner as preferred method (this work: ✅)
2. Log warnings when scheduler used (future)
3. Remove scheduler kaspi_autosync job (future major version)
