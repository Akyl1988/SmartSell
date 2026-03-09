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
