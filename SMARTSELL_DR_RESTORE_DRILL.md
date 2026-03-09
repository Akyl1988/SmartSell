# SMARTSELL_DR_RESTORE_DRILL

## 1 Purpose
Document the first practical disaster recovery restore drill for SmartSell and define how service can be restored after major failure for first-client operations.

## 2 Failure scenarios
- Primary database unavailable/corrupted.
- Application deployment failure causing prolonged outage.
- Infrastructure-level failure requiring rebuild from backups.
- Misconfiguration causing service startup failure after release.

## 3 Backup sources
- Database backup source: TBD (latest verified snapshot location).
- Application artifact/image source: TBD.
- Environment/config backup source (secrets/config templates): TBD.
- Timestamp of backup selected for drill: Not yet executed.

## 4 Restore procedure
1. Declare DR drill start and assign owner.
2. Freeze writes to affected environment (if applicable).
3. Provision/prepare restore target environment.
4. Restore database from selected backup.
5. Deploy last known good SmartSell artifact.
6. Apply required runtime configuration/secrets.
7. Start API, worker, and required dependencies.
8. Record timestamps for each step.

## 5 Verification steps
- [ ] API health endpoint returns success.
- [ ] Authentication/login works for admin account.
- [ ] One tenant can read/write a core business flow.
- [ ] Background worker/scheduler is running.
- [ ] Critical integration path responds (Kaspi sanity check).
- [ ] No critical startup/migration errors in logs.

Status: Not yet executed.

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

Current evidence status: **Not yet executed / Pending evidence**.

## 9 Follow-up improvements
1. Automate restore steps into a runnable script/checklist.
2. Add periodic backup integrity validation.
3. Re-run drill and compare achieved RPO/RTO vs targets.
4. Capture known bottlenecks and remove manual steps.
