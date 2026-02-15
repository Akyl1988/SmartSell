# Campaigns Scheduler Modes

This runbook defines how campaigns are processed in dev/test versus production.

## Dev/Test manual mode

Use manual endpoints and keep the scheduler off.

1) Disable scheduler

```
$env:ENABLE_SCHEDULER="0"
```

2) Start API

```
uvicorn app.main:app --reload
```

3) Run smoke (manual tick/run)

```
pwsh -NoProfile -File .\scripts\smoke-campaigns-run.local.ps1 -CompanyId <id>
pwsh -NoProfile -File .\scripts\smoke-campaigns-e2e.ps1 -CompanyId <id>
```

Notes:
- The smoke scripts accept `-Identifier` and `-Password` or load cached tokens from `.smoke-cache.json`.
- Dev/test endpoints are only available when `ENVIRONMENT` is not `production`.

## Production mode

- Dev/test endpoints return 404.
- Scheduler runs only when `ENABLE_SCHEDULER=1` (and the process role allows it).
- Kaspi sync runner starts only when `ENABLE_KASPI_SYNC_RUNNER=1`.

## Success criteria

- `python -m ruff check .` and `python -m pytest -q` are green.
- Both smoke scripts pass in dev/test with `ENABLE_SCHEDULER=0`.
- Dev/test endpoints return 404 when `ENVIRONMENT=production`.
