# OPERATOR_DEPLOY_TRANSCRIPT_2026-03-09

Назначение: единый операторский transcript production-like deploy/restart rehearsal, заполненный по уже зафиксированным evidence.

## 1. Rehearsal metadata
- timestamp: `2026-03-09 19:04:25 +05:00`
- branch: `feat/incident-followups`
- commit: `ae8f15a`
- python version: `3.11.9`

## 2. Deploy commands executed
- В rehearsal evidence зафиксирована проверка deploy/runbook consistency через `docs/DEPLOY_MINIMAL_PROD.md` (команды обнаружены через `Select-String`).
- Referenced deploy commands:
  - `docker compose -f docker-compose.prod.yml up -d --build`
  - `docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head`

## 3. Runtime verification
- `GET /api/v1/health` -> `200`
- `GET /ready` -> `200`

## 4. Smoke verification
- В deploy consistency evidence зафиксированы smoke references в `docs/DEPLOY_MINIMAL_PROD.md`:
  - `scripts/smoke-auth.ps1`
  - `scripts/smoke-preorders-e2e.ps1`

## 5. Role separation verification
- `tests/test_process_role_gating.py` focused set -> `4 passed in 7.62s`
- `tests/test_core_startup_hook_guards.py` focused set -> `2 passed in 6.79s`

## 6. Observation window
- status: `no runtime errors observed during the rehearsal`
- note: В linked deploy/startup rehearsal evidence нет отдельного 10–15 minute таймбокса с start/finish для этого конкретного пакета.

## 7. Rollback decision
- rollback required? `no`
- reason:
  - Runtime verification (`/api/v1/health`, `/ready`) and role separation checks passed in linked rehearsal evidence.

## 8. Operator sign-off
- Operator: `Founder`
- Status: `rehearsal successful`

## 9. Runtime ownership operator cycle #2 (2026-03-09 22:31 +05)

### 9.1 Metadata
- timestamp: `2026-03-09 22:31:04 +05:00`
- branch: `feat/incident-followups`
- commit: `4eed667`
- python version: `3.11.9`

### 9.2 Runtime startup commands (executed)
- API/web role:
  - `$env:PROCESS_ROLE='web'; $env:ENABLE_SCHEDULER='0'; $env:ENABLE_KASPI_SYNC_RUNNER='0'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010`
- Scheduler role:
  - `$env:PROCESS_ROLE='scheduler'; $env:ENABLE_SCHEDULER='1'; $env:ENABLE_KASPI_SYNC_RUNNER='0'; $env:KASPI_AUTOSYNC_ENABLED='true'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011`
- Runner role:
  - `$env:PROCESS_ROLE='runner'; $env:ENABLE_SCHEDULER='0'; $env:ENABLE_KASPI_SYNC_RUNNER='1'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8012`

### 9.3 Health/readiness results
- `http://127.0.0.1:8010/api/v1/health` -> `200`
- `http://127.0.0.1:8010/ready` -> `200`
- `http://127.0.0.1:8011/api/v1/health` -> `200`
- `http://127.0.0.1:8011/ready` -> `200`
- `http://127.0.0.1:8012/api/v1/health` -> `200`
- `http://127.0.0.1:8012/ready` -> `200`

### 9.4 Ownership boundary verification
- Role-gating tests:
  - `tests/test_process_role_gating.py` focused set -> `4 passed in 8.21s`
- Startup-hook tests:
  - `tests/test_core_startup_hook_guards.py` focused set -> `2 passed in 6.86s`

### 9.5 Observation window
- window start: `2026-03-09 22:32:31 +05:00`
- window finish: `2026-03-09 22:42:32 +05:00`
- duration: `10,02` minutes
- status: `incident-free`
- note:
  - scheduler logs show periodic scheduler worker ticks.
  - web role logs for the same window show health/readiness access logs and no scheduler tick lines.

### 9.6 Rollback decision
- rollback required? `no`
- reason:
  - role processes stayed healthy during the full window;
  - ownership boundary checks passed;
  - no runtime incident observed.

### 9.7 Operator sign-off
- operator: `Founder`
- outcome: `accepted`
