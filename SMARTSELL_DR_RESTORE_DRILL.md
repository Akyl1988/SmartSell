# SMARTSELL_DR_RESTORE_DRILL

## 1 Purpose
Document the first practical disaster recovery restore drill for SmartSell and define how service can be restored after major failure for first-client operations.

## 2 Failure scenarios
- Primary database unavailable/corrupted.
- Application deployment failure causing prolonged outage.
- Infrastructure-level failure requiring rebuild from backups.
- Misconfiguration causing service startup failure after release.

## 3 Backup sources
- Database backup command used:
	- `pg_dump -U postgres -h 127.0.0.1 -p 5432 -d smartsell_main -f .\\tmp\\drill\\smartsell_main_drill.sql`
- Output artifact path:
	- `tmp/drill/smartsell_main_drill.sql`
- Artifact size/timestamp:
	- Generated locally during DR drill.
	- File exists in `tmp/drill` and contains a full PostgreSQL dump.
- Application artifact/image source:
	- Not yet executed as part of this DB-focused drill.
- Environment/config backup source:
	- Not yet executed as part of this DB-focused drill.

## 4 Restore procedure
1. Declare DR drill start and assign owner.
2. Freeze writes to affected environment (if applicable).
3. Provision/prepare restore target environment.
4. Restore database from selected backup.
5. Deploy last known good SmartSell artifact.
6. Apply required runtime configuration/secrets.
7. Start API, worker, and required dependencies.
8. Record timestamps for each step.

Execution evidence for this drill:
- Restore was executed: **Yes**.
- Restore command used:
	- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -f .\\tmp\\drill\\smartsell_main_drill.sql`

## 5 Verification steps
- [x] Database restore command completed.
- [x] Table listing verification completed.
- [x] API health/readiness checks passed in restore verification set.
- [x] Authentication/login flow passed in restore verification set.
- [x] Tenant diagnostics endpoint check passed in restore verification set.
- [x] One critical tenant core flow passed in restore verification set.
- [x] Worker/scheduler readiness checks passed in restore verification set.
- [ ] Critical integration path responds (Kaspi live sanity check against restored environment). *(Not yet verified in this drill set)*

Verification commands used:
- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -f .\\tmp\\drill\\smartsell_main_drill.sql`
- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -c "\\dt"`
- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_health_and_ready.py::test_health_ok tests/test_health_and_ready.py::test_ready_relaxed_200 tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation tests/test_process_role_gating.py::test_scheduler_starts_for_scheduler_role tests/test_process_role_gating.py::test_kaspi_runner_starts_for_runner_role -q`
- `D:/LLM_HUB/SmartSell/.venv/Scripts/python.exe -m pytest tests/test_upgrade_playbook_docs.py::test_upgrade_playbook_docs_contains_key_strings -q`
- `Select-String -Path "docs/UPGRADE_PLAYBOOK.md" -Pattern "backup_db.ps1|restore_db.ps1|/api/v1/health|/ready"`
- `Select-String -Path "docs/DEPLOY_MINIMAL_PROD.md" -Pattern "/api/v1/health|/ready|smoke-auth.ps1|smoke-preorders-e2e.ps1"`
- `Select-String -Path "docs/runbooks/add_new_company.md" -Pattern "smoke-preorders-e2e.ps1|POST /api/v1/repricing/run|/api/v1/auth/me"`
- `Select-String -Path "SMARTSELL_ONBOARDING_PLAYBOOK.md" -Pattern "diagnostics|login|core flow|Rollback"`

Verification result:
- Database restored successfully and 71 tables detected.
- Application-level restore verification set executed on 2026-03-09 18:27:26 +05:00.
- Result: `7 passed in 13.47s` (health/readiness, auth login, diagnostics, critical tenant flow, worker/scheduler gating).
- Runbook consistency check result: `1 passed in 6.57s` and required restore/health/smoke references were found in upgrade/deploy/onboarding runbooks.

## 6 RPO target
- Target RPO: **15 minutes** (initial operating target).
- Achieved in this drill: Pending evidence.

## 7 RTO target
- Target RTO: **60 minutes** (initial operating target).
- Achieved in this drill: Pending evidence.

## 8 Evidence required
- Drill date/time and incident owner.
- Backup identifier used (snapshot/file/version).
- Restore command outputs/log excerpts.
- Service health verification outputs.
- Measured restore duration (start → service healthy).
- Measured data gap against backup timestamp.

Current evidence status: **Backup + DB restore evidence completed; application-level restore verification evidence added for health/auth/diagnostics/core flow/worker-scheduler; live integration sanity and repeated restore-cycle evidence remain pending**.

## 9 Issues found
- No blocking issues.
- Restore completed successfully.
- No destructive cleanup was performed in this drill step.

## 10 Final outcome
- Backup and restore drill executed successfully at database level.
- PostgreSQL dump restored into database `smartsell_drill_restore`.
- Schema integrity verified via table listing (`71` tables).
- Application-level restore readiness verified via executed checks for:
	- API health/readiness
	- auth/login
	- tenant diagnostics endpoint
	- critical tenant preorder flow
	- worker/scheduler role gating readiness
- This is still not full production-like restore proof; repeated restore cycles and live integration verification are required before `Exists`.

## 11 Follow-up actions
1. Automate backup process and schedule periodic DR drills.
2. Store backup artifacts outside the application host for disaster recovery readiness.
3. Execute Kaspi live sanity check against restored target and attach output.
4. Collect repeated restore-cycle evidence (at least one additional full cycle with timing data).
