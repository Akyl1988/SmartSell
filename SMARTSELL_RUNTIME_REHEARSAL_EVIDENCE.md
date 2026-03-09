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
