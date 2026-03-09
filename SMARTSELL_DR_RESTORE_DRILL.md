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
- [ ] API health endpoint returns success. *(Pending application-level restore verification)*
- [ ] Authentication/login works for admin account. *(Pending application-level restore verification)*
- [ ] One tenant can read/write a core business flow. *(Pending application-level restore verification)*
- [ ] Background worker/scheduler is running. *(Pending application-level restore verification)*
- [ ] Critical integration path responds (Kaspi sanity check). *(Pending application-level restore verification)*

Verification commands used:
- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -f .\\tmp\\drill\\smartsell_main_drill.sql`
- `psql -U postgres -h 127.0.0.1 -p 5432 -d smartsell_drill_restore -c "\\dt"`

Verification result:
- Database restored successfully and 71 tables detected.

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

Current evidence status: **Backup and DB restore evidence completed; full application-level restore verification still pending**.

## 9 Issues found
- No blocking issues.
- Restore completed successfully.

## 10 Final outcome
- Backup and restore drill executed successfully at database level.
- PostgreSQL dump restored into database `smartsell_drill_restore`.
- Schema integrity verified via table listing (`71` tables).
- Full service-level restore step is still pending; backup evidence is complete.
- Full restore evidence is still required before this DR track can move to `Exists`.

## 11 Follow-up actions
1. Automate backup process and schedule periodic DR drills.
2. Store backup artifacts outside the application host for disaster recovery readiness.
3. Execute full application-level restore verification (API, auth, tenant flow, worker, integrations).
