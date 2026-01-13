# Process roles (PROCESS_ROLE) — runbook

Цель: исключить двойной старт Kaspi autosync (scheduler job vs runner loop).

## Роли

### PROCESS_ROLE=web
- Назначение: API (основной веб-процесс).
- Scheduler: НЕ стартует (даже если ENABLE_SCHEDULER=1).
- Kaspi runner: стартует ТОЛЬКО если ENABLE_KASPI_SYNC_RUNNER=1.

### PROCESS_ROLE=scheduler
- Назначение: запуск APScheduler job'ов.
- Scheduler: стартует ТОЛЬКО если ENABLE_SCHEDULER=1.
- Kaspi runner: НЕ стартует (даже если ENABLE_KASPI_SYNC_RUNNER=1).
- Kaspi autosync job регистрируется только в этой роли (при KASPI_AUTOSYNC_ENABLED=true и runner выключен).

### PROCESS_ROLE=runner
- Назначение: отдельный процесс для Kaspi runner loop (если нужно отделить от web).
- Scheduler: НЕ стартует.
- Kaspi runner: стартует ТОЛЬКО если ENABLE_KASPI_SYNC_RUNNER=1.

## Флаги

- ENABLE_SCHEDULER=1 — разрешает старт scheduler (только при PROCESS_ROLE=scheduler).
- KASPI_AUTOSYNC_ENABLED=true — разрешает регистрацию kaspi_autosync job (только при PROCESS_ROLE=scheduler).
- ENABLE_KASPI_SYNC_RUNNER=1 — разрешает runner loop (только при PROCESS_ROLE in (web, runner)).

## Гарантия взаимного исключения
- Kaspi autosync никогда не стартует дважды:
  - scheduler job регистрируется только при PROCESS_ROLE=scheduler
  - runner loop запускается только при PROCESS_ROLE in (web, runner)
  - дополнительно: если ENABLE_KASPI_SYNC_RUNNER=1, scheduler job для kaspi_autosync не регистрируется

## Примеры запуска (Windows PowerShell)

### Web (API)
$env:PROCESS_ROLE="web"
$env:ENABLE_SCHEDULER="0"
$env:ENABLE_KASPI_SYNC_RUNNER="0"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

### Scheduler процесс (отдельным портом)
$env:PROCESS_ROLE="scheduler"
$env:ENABLE_SCHEDULER="1"
$env:ENABLE_KASPI_SYNC_RUNNER="0"
$env:KASPI_AUTOSYNC_ENABLED="true"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

### Runner процесс (если отделяем от web)
$env:PROCESS_ROLE="runner"
$env:ENABLE_SCHEDULER="0"
$env:ENABLE_KASPI_SYNC_RUNNER="1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
