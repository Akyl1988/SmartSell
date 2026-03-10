# SMARTSELL_RELEASE_DRY_RUN_EVIDENCE

## 1. Purpose
Record operational dry-run evidence for `SMARTSELL_RELEASE_CHECKLIST.md` using real executed commands and observed outputs.

## 2. Release date
- 2026-03-09 18:08:29 +05:00

## 3. Environment
- Workspace: `D:\LLM_HUB\SmartSell`
- OS: Windows
- Python: `3.11.9` (`.venv`)
- Branch: `feat/incident-followups`
- Commit: `68376d5`

## 4. Steps executed

### 4.1 Deploy preparation
- Checked repository state and release metadata:
	- `git rev-parse --abbrev-ref HEAD`
	- `git rev-parse --short HEAD`
	- `git status -sb`
	- `Get-Date -Format "yyyy-MM-dd HH:mm:ss K"`
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe --version`
- Observed:
	- Branch: `feat/incident-followups`
	- Commit: `68376d5`
	- Python: `3.11.9`
	- Working tree includes untracked `docs/plans/`.

### 4.2 Migration verification
- Command:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_migration_upgrade.py::test_alembic_upgrade_head_runs -q`
- Observed output:
	- `1 passed in 6.75s`

### 4.3 Smoke test execution
- Command:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/test_auth.py::TestAuth::test_logout_revokes_session tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/test_campaign_runner.py::test_enqueue_due_campaigns_queues_ready tests/app/test_campaign_processing_worker.py::test_campaign_worker_transitions_to_done tests/app/test_repricing_scheduler.py::test_repricing_autorun_runs_when_enabled -q`
- Observed output:
	- `6 passed in 10.08s`

### 4.4 Tenant diagnostics verification
- Included in smoke command via:
	- `tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary`
- Result:
	- PASS (part of `6 passed in 10.08s`)

### 4.5 Repricing check
- Included in smoke command via:
	- `tests/app/test_repricing_scheduler.py::test_repricing_autorun_runs_when_enabled`
- Result:
	- PASS (part of `6 passed in 10.08s`)

### 4.6 Campaign pipeline check
- Included in smoke command via:
	- `tests/app/test_campaign_runner.py::test_enqueue_due_campaigns_queues_ready`
	- `tests/app/test_campaign_processing_worker.py::test_campaign_worker_transitions_to_done`
- Result:
	- PASS (part of `6 passed in 10.08s`)

### 4.7 Rollback readiness
- Command block:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings -q`
	- `Test-Path "tmp/drill/smartsell_main_drill.sql"`
	- `Select-String -Path "SMARTSELL_RELEASE_CHECKLIST.md" -Pattern "Rollback procedure"`
	- `Select-String -Path "docs/UPGRADE_PLAYBOOK.md" -Pattern "Rollback|restore_db.ps1|backup_db.ps1"`
- Observed output:
	- `1 passed in 6.32s`
	- `True` for `tmp/drill/smartsell_main_drill.sql`
	- Rollback sections/commands present in checklist and upgrade playbook.

## 5. Confirmations
- migrations verified: **Yes** (`test_alembic_upgrade_head_runs` passed).
- API smoke tests passed: **Yes** (`6 passed in 10.08s`).
- diagnostics endpoint responding: **Yes** (`test_admin_tenant_diagnostics_summary` passed).
- campaign pipeline healthy: **Yes** (campaign queue + processing worker checks passed).
- repricing runner healthy: **Yes** (`test_repricing_autorun_runs_when_enabled` passed).
- rollback plan validated: **Yes** (rollback playbook test passed; drill artifact present; rollback sections detected).

## 6. Issues found
- No blocking failures in this dry-run execution.
- Note: working tree included untracked `docs/plans/` during preparation snapshot.

## 7. Final outcome
- Outcome: PASS (simulated release cycle executed end-to-end with evidence captured).
- Current state: Release checklist is operationally exercised once; repeat cycles still required for maturity.

## 8. Follow-up actions
1. Repeat dry-run for at least one additional cycle and archive evidence pack.
2. Add one production-like rehearsal with explicit API/worker restart log excerpts.
3. Keep rollback rehearsal evidence linked to each release record.

## 9. Release cycle #2 (repeatability evidence)

### 9.1 Release date
- 2026-03-09 18:33:06 +05:00

### 9.2 Environment
- Workspace: `D:\LLM_HUB\SmartSell`
- OS: Windows
- Python: `3.11.9` (`.venv`)
- Branch: `feat/incident-followups`
- Commit: `414d68c`

### 9.3 Steps executed

#### 9.3.1 Release-style preparation evidence
- Command block:
	- `git rev-parse --abbrev-ref HEAD`
	- `git rev-parse --short HEAD`
	- `git status -sb`
	- `Get-Date -Format "yyyy-MM-dd HH:mm:ss K"`
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe --version`
- Observed:
	- Branch: `feat/incident-followups`
	- Commit: `414d68c`
	- Python: `3.11.9`
	- Working tree includes untracked `docs/plans/`.

#### 9.3.2 Migration verification
- Command:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_migration_upgrade.py::test_alembic_upgrade_head_runs -q`
- Observed output:
	- `1 passed in 6.93s`

#### 9.3.3 Smoke verification
- Command:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_health_and_ready.py::test_health_ok tests/test_health_and_ready.py::test_ready_relaxed_200 tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role -q`
- Observed output:
	- `7 passed in 12.70s`

#### 9.3.4 Diagnostics verification
- Covered by:
	- `tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary`
- Result:
	- PASS (included in `7 passed in 12.70s`)

#### 9.3.5 One critical tenant flow
- Covered by:
	- `tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation`
- Result:
	- PASS (included in `7 passed in 12.70s`)

#### 9.3.6 Rollback readiness verification
- Command block:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings -q`
	- `Test-Path "tmp/drill/smartsell_main_drill.sql"`
	- `Select-String -Path "SMARTSELL_RELEASE_CHECKLIST.md" -Pattern "Rollback procedure"`
	- `Select-String -Path "docs/UPGRADE_PLAYBOOK.md" -Pattern "Rollback|restore_db.ps1|backup_db.ps1|/api/v1/health|/ready"`
	- `Select-String -Path "SMARTSELL_DR_RESTORE_DRILL.md" -Pattern "smartsell_drill_restore|tmp/drill/smartsell_main_drill.sql"`
- Observed output:
	- `1 passed in 6.44s`
	- `True` for `tmp/drill/smartsell_main_drill.sql`
	- Rollback procedure and restore/health references are present and consistent across checklist/runbooks.

#### 9.3.7 Onboarding-style verification reference
- Command:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation -q`
- Observed output:
	- `3 passed in 10.74s`

### 9.4 Confirmations (Cycle #2)
- migrations verified: **Yes**.
- API smoke verification passed: **Yes**.
- diagnostics endpoint verification passed: **Yes**.
- one critical tenant flow verification passed: **Yes**.
- worker/scheduler readiness verification passed: **Yes**.
- rollback readiness documentation consistency verified: **Yes**.
- onboarding-style compact verification executed: **Yes**.

### 9.5 Outcome (Cycle #2)
- Outcome: PASS.
- Repeatability status: second compact operational cycle evidence collected and archived in this document.
- Remaining gap before `Exists`: production-like release evidence with explicit deploy/restart logs and repeated cycles beyond test-environment rehearsal.

## 10. Production-like operational rehearsal (runtime-command evidence)

### 10.1 Rehearsal date
- 2026-03-09 18:41:02 +05:00

### 10.2 Environment
- Workspace: `D:\LLM_HUB\SmartSell`
- OS: Windows
- Python: `3.11.9` (`.venv`)
- Branch: `feat/incident-followups`
- Commit: `e9699f0`

### 10.3 Runtime ownership split evidence
- Command:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_scheduler_skipped_for_web_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role tests/test_process_role_gating.py::test_kaspi_runner_skipped_for_scheduler_role -q`
- Observed output:
	- `4 passed in 8.08s`

### 10.4 Release-style operational evidence
- Migration verification:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_migration_upgrade.py::test_alembic_upgrade_head_runs -q`
	- Output: `1 passed in 6.58s`
- Health/readiness runtime probes:
	- `Invoke-WebRequest ... /api/v1/health` -> `200`
	- `Invoke-WebRequest ... /ready` -> `200`
	- `Invoke-WebRequest ... /api/v1/wallet/health` -> `200`
- Compact smoke/diagnostics/critical flow verification:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_health_and_ready.py::test_ready_relaxed_200 tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation -q`
	- Output: `4 passed in 11.43s`

### 10.5 Rollback / restore readiness evidence
- Command block:
	- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings -q`
	- `Test-Path "tmp/drill/smartsell_main_drill.sql"`
	- `Select-String -Path "docs/UPGRADE_PLAYBOOK.md" -Pattern "backup_db.ps1|restore_db.ps1|/api/v1/health|/ready"`
	- `Select-String -Path "docs/DEPLOY_MINIMAL_PROD.md" -Pattern "curl -fsS http://127.0.0.1:8000/api/v1/health|curl -fsS http://127.0.0.1:8000/ready|smoke-auth.ps1|smoke-preorders-e2e.ps1"`
	- `Select-String -Path "SMARTSELL_DR_RESTORE_DRILL.md" -Pattern "smartsell_drill_restore|tmp/drill/smartsell_main_drill.sql|Application-level restore verification"`
- Observed output:
	- `1 passed in 6.38s`
	- `True` for `tmp/drill/smartsell_main_drill.sql`
	- Restore/rollback and health/smoke references found across release/deploy/DR artifacts.

### 10.6 Linked evidence
- Runtime rehearsal details: `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md`
- DR drill baseline: `SMARTSELL_DR_RESTORE_DRILL.md`

## 11. Full restore-oriented production-like cycle (measured)

### 11.1 Timing
- Start timestamp: `2026-03-09 18:54:13 +05:00`
- Finish timestamp: `2026-03-09 18:54:35 +05:00`
- Measured duration: `21.88` seconds

### 11.2 Commands executed and observed outputs
- Restore command:
	- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -f .\\tmp\\drill\\smartsell_main_drill.sql` -> `RESTORE_EXIT=0`
	- Verbose output archived in `tmp/drill/dr_cycle4_restore.log`
- Restore verification command:
	- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -c "\\dt"` -> `DT_EXIT=0`
	- Output archived in `tmp/drill/dr_cycle4_dt.log`
- Health/readiness probes:
	- `/api/v1/health` -> `200`
	- `/ready` -> `200`
- Application-level post-restore verification:
	- `pytest tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role -q`
	- Output: `5 passed in 11.10s`
	- Exit: `POST_RESTORE_TEST_EXIT=0`
- Migration + rollback readiness checks:
	- `pytest tests/test_migration_upgrade.py::test_alembic_upgrade_head_runs tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings -q`
	- Output: `2 passed in 6.74s`
	- Exit: `MIGRATION_ROLLBACK_TEST_EXIT=0`
- Restore artifact presence:
	- `Test-Path tmp/drill/smartsell_main_drill.sql` -> `True`

### 11.3 Live integration sanity
- Attempted:
	- `Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v1/kaspi/status -TimeoutSec 10`
- Observed:
	- `KASPI_STATUS_ERROR=Response status code does not indicate success: 401 (Unauthorized).`
- Conclusion:
	- Live integration sanity remains unconfirmed in this rehearsal.

## 12. Deploy/startup production-like rehearsal linkage

### 12.1 Rehearsal metadata
- Timestamp: `2026-03-09 19:04:25 +05:00`
- Branch: `feat/incident-followups`
- Commit: `ae8f15a`

### 12.2 Runtime/deploy evidence summary
- API role responsiveness:
	- `/api/v1/health` -> `200`
	- `/ready` -> `200`
- Role separation checks:
	- `tests/test_process_role_gating.py` focused set -> `4 passed in 7.62s`
- Startup-hook boundary checks:
	- `tests/test_core_startup_hook_guards.py` focused set -> `2 passed in 6.79s`
- Release/deploy docs consistency:
	- `tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings` -> `1 passed in 6.64s`
	- `Select-String` confirmed deployment/migration/health/smoke commands in `docs/DEPLOY_MINIMAL_PROD.md`

### 12.3 Linked evidence
- Runtime evidence pack: `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md` (Section 7)

### 12.4 Known limitation
- Live integration sanity check still returns unauthorized in local context:
	- `kaspi_status_error: Response status code does not indicate success: 401 (Unauthorized).`

## 13. Final release-gate rehearsal (2026-03-09)

### 13.1 Metadata
- Start timestamp: `2026-03-09 20:11:54 +05:00`
- Continuation start: `2026-03-09 20:12:15 +05:00`
- Finish timestamp: `2026-03-09 20:12:28 +05:00`
- Branch: `feat/incident-followups`
- Commit: `3622a57`
- Python: `3.11.9`

### 13.2 Runtime endpoint verification
- `GET /api/v1/health` -> `200`
- `GET /ready` -> `200`
- `GET /api/v1/wallet/health` -> `200`

### 13.3 Smoke scripts
- `scripts/smoke-auth.ps1` -> `SMOKE_AUTH_EXIT=0`
- `scripts/smoke-preorders-e2e.ps1` -> failed with:
	- `Create product failed: status=402`
	- `"code": "SUBSCRIPTION_REQUIRED"`
	- `"code": "HTTP_402"`
- `scripts/smoke-orders-lifecycle.ps1` -> failed with:
	- `Create product failed: status=402`
	- `"code": "SUBSCRIPTION_REQUIRED"`
	- `"code": "HTTP_402"`

### 13.4 Minimal Kaspi sanity (authenticated)
- Runtime authenticated check used valid Bearer JWT in tenant context.
- Observed output:
	- `KASPI_RUNTIME_USER_ID=1`
	- `KASPI_RUNTIME_USER_ROLE=admin`
	- `KASPI_RUNTIME_USER_COMPANY_ID=1`
	- `KASPI_STATUS_HTTP=200`
	- `KASPI_RUNTIME_EXIT=0`

### 13.5 Worker role gating verification
- `pytest tests/test_process_role_gating.py -q`
- Output: `8 passed in 9.30s`
- Exit: `ROLE_GATING_EXIT=0`

### 13.6 Observation window
- Continuation window duration: `12.57` seconds (`RELEASE_GATE_CONT_DURATION_SECONDS=12.57`).
- No runtime endpoint errors were observed during the rehearsal window (`health/ready/wallet/kaspi status` checks succeeded).
- Release gate outcome is still **FAIL** due to business precondition failure in smoke scripts (`SUBSCRIPTION_REQUIRED`, HTTP 402).

### 13.7 Final gate decision
- `Release checklist and smoke gate` is **not promoted to Exists** in this cycle.
- Exact missing prerequisite:
	- tenant must satisfy subscription/wallet activation precondition required for product-creating smoke flows.

## 14. Release-gate blocker fix validation (2026-03-09)

### 14.1 Implemented operational fix
- Added tenant preflight check for product-creating smoke flows (no billing guard bypass):
	- `scripts/smoke-preorders-e2e.ps1`
	- `scripts/smoke-orders-lifecycle.ps1`
	- shared helper in `scripts/_smoke-lib.ps1`: `Test-SmokeTenantProductCreatePreflight`
- Preflight uses read-only protected probe:
	- `GET /api/v1/products?page=1&per_page=1`
- Behavior:
	- if subscription is valid -> smoke continues;
	- if `HTTP 402 / SUBSCRIPTION_REQUIRED` -> smoke fails immediately with explicit remediation before product creation.

### 14.2 Focused validation
- `pytest tests/test_smoke_subscription_preflight_scripts.py -q`
	- `3 passed in 7.57s`

### 14.3 Runtime verification (current tenant state)
- `scripts/smoke-preorders-e2e.ps1` observed output:
	- `SMOKE_PRECHECK_SUBSCRIPTION_BLOCK: status=trialing period_end=03/03/2026 20:54:18 grace_until=03/07/2026 05:00:00 (HTTP_402/SUBSCRIPTION_REQUIRED).`
- `scripts/smoke-orders-lifecycle.ps1` observed output:
	- `SMOKE_PRECHECK_SUBSCRIPTION_BLOCK: status=trialing period_end=03/03/2026 20:54:18 grace_until=03/07/2026 05:00:00 (HTTP_402/SUBSCRIPTION_REQUIRED).`

### 14.4 Outcome
- Root cause is fixed operationally (hidden subscription-state failure removed).
- Gate remains blocked by real business prerequisite only:
	- prepare tenant subscription/wallet (e.g., `scripts/smoke-billing-trial.ps1`) before release smoke run.

## 15. Prepared tenant release-gate rehearsal (2026-03-09)

### 15.1 Rehearsal metadata
- Start timestamp: `2026-03-09 20:38:06 +05:00`
- Finish timestamp: `2026-03-09 20:38:20 +05:00`
- Duration: `13.9` seconds
- Branch: `feat/incident-followups`
- Commit: `3622a57`
- Python: `3.11.9`

### 15.2 Tenant preparation (source of truth)
- Preparation script:
	- `scripts/smoke-tenant-prepare.ps1 -BaseUrl http://127.0.0.1:8000`
- Observed output:
	- `TENANT_COMPANY_ID=1`
	- `PREVIOUS_SUBSCRIPTION_STATE=status:active;plan:Pro;id:7;period_end:;grace_until:`
	- `ACTION_TAKEN=none`
	- `RESULTING_SUBSCRIPTION_STATE=status:active;plan:Pro;id:7;period_end:;grace_until:`
	- `SMOKE_ALLOWED=True`
	- `TENANT_PREPARE_EXIT=0`

### 15.3 Runtime endpoint verification
- `GET /api/v1/health` -> `200`
- `GET /ready` -> `200`
- `GET /api/v1/wallet/health` -> `200`

### 15.4 Smoke execution
- `scripts/smoke-auth.ps1` -> `SMOKE_AUTH_EXIT=0`
- `scripts/smoke-preorders-e2e.ps1`:
	- preflight: `[INFO] Subscription preflight OK: status=eligible`
	- completion: `OK: preorder e2e complete`
	- exit: `SMOKE_PREORDERS_EXIT=0`
- `scripts/smoke-orders-lifecycle.ps1`:
	- preflight: `[INFO] Subscription preflight OK: status=eligible`
	- completion: `OK: order lifecycle smoke complete`
	- exit: `SMOKE_ORDERS_LIFECYCLE_EXIT=0`

### 15.5 Minimal Kaspi sanity (authenticated)
- Observed output:
	- `KASPI_RUNTIME_USER_ID=1`
	- `KASPI_RUNTIME_USER_ROLE=admin`
	- `KASPI_RUNTIME_USER_COMPANY_ID=1`
	- `KASPI_STATUS_HTTP=200`

### 15.6 Worker role gating
- `pytest tests/test_process_role_gating.py -q`
- Output: `8 passed in 9.72s`
- Exit: `ROLE_GATING_EXIT=0`

### 15.7 Final outcome
- Release-gate rehearsal on prepared tenant: **PASS**.
- Product-creating smoke flows pass end-to-end with preflight preserved.
- Billing/subscription enforcement remains active; no guard bypass applied.
