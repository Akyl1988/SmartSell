# SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE

## 1. Purpose
Capture production-like runtime ownership rehearsal evidence using executed commands and observed outputs only.

## 2. Rehearsal metadata
- Date/time: 2026-03-09 18:41:02 +05:00
- Workspace: `D:\LLM_HUB\SmartSell`
- Branch: `feat/incident-followups`
- Commit: `e9699f0`
- Python: `3.11.9`

## 3. Runtime role separation checks
Command:

`D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_scheduler_skipped_for_web_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role tests/test_process_role_gating.py::test_kaspi_runner_skipped_for_scheduler_role -q`

Observed output:
- `4 passed in 8.08s`

What this verifies:
- scheduler role starts scheduler path, web role does not.
- runner role starts Kaspi runner path, scheduler role does not.

## 4. Runtime health/readiness probes
Command:

`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v1/health`
`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/ready`
`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v1/wallet/health`

Observed output:
- `/api/v1/health` -> `200`
- `/ready` -> `200`
- `/api/v1/wallet/health` -> `200`

## 5. Notes
- This rehearsal is production-like operational evidence in local runtime context.
- It does not replace repeated production deploy records with explicit process startup logs.

## 6. Rehearsal cycle #2 (full restore-oriented context)

### 6.1 Timing
- Start timestamp: `2026-03-09 18:54:13 +05:00`
- Finish timestamp: `2026-03-09 18:54:35 +05:00`
- Measured duration: `21.88` seconds

### 6.2 Runtime role readiness checks
Command:

`pytest tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role -q`

Observed output (as part of post-restore verification bundle):
- Included in: `5 passed in 11.10s`

### 6.3 Runtime endpoint readiness
Observed output:
- `/api/v1/health` -> `200`
- `/ready` -> `200`

### 6.4 Notes
- This cycle strengthens runtime operational evidence by combining role readiness and live endpoint checks in a measured restore-oriented rehearsal.
- Production deploy/startup logs are still required for `Exists` level.

## 7. Production-like deploy/startup rehearsal pack (2026-03-09)

### 7.1 Rehearsal metadata
- Timestamp: `2026-03-09 19:04:25 +05:00`
- Branch: `feat/incident-followups`
- Commit: `ae8f15a`
- Python: `3.11.9`

### 7.2 API role evidence
Commands:

`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v1/health`
`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/ready`

Observed output:
- `/api/v1/health` -> `200`
- `/ready` -> `200`

### 7.3 Scheduler/runner separation evidence
Command:

`D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_scheduler_skipped_for_web_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role tests/test_process_role_gating.py::test_kaspi_runner_skipped_for_scheduler_role -q`

Observed output:
- `4 passed in 7.62s`

### 7.4 Startup-hook boundary evidence
Command:

`D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_core_startup_hook_guards.py::test_startup_skipped_for_non_web_role tests/test_core_startup_hook_guards.py::test_startup_web_role_respects_migration_flag -q`

Observed output:
- `2 passed in 6.79s`

### 7.5 Deploy/runbook consistency evidence
Command block:
- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings -q`
- `Select-String -Path "docs/DEPLOY_MINIMAL_PROD.md" -Pattern "docker compose -f docker-compose.prod.yml up -d --build|alembic upgrade head|/api/v1/health|/ready|smoke-auth.ps1|smoke-preorders-e2e.ps1"`

Observed output:
- `1 passed in 6.64s`
- Deployment/migration/health/smoke command references found in `docs/DEPLOY_MINIMAL_PROD.md`.

### 7.6 Live integration sanity attempt
Command:

`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v1/kaspi/status -TimeoutSec 10`

Observed output:
- `kaspi_status_error: Response status code does not indicate success: 401 (Unauthorized).`

Status:
- Live integration sanity is not confirmed in this rehearsal pack.
