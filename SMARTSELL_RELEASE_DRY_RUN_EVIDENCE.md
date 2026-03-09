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
