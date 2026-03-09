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
