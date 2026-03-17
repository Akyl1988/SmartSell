# DR_ACCEPTANCE_2026-03-14

- Date: 2026-03-14
- Candidate branch: dev
- Candidate commit: 52c498b
- Owner: Ақыл
- Rollback Owner: Ақыл

## 1. Existing evidence reviewed

- SMARTSELL_DR_RESTORE_DRILL.md
- SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md
- SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md
- PRODUCTION_DEPLOYMENT_CHECKLIST.md

## 2. Launch acceptance intent

This DR acceptance is defined for the first-client controlled launch window only.
The goal is not perfect enterprise DR maturity, but explicit rollback and recovery decision rules for safe onboarding.

## 3. Accepted launch-window RPO / RTO

- Accepted RPO for first-client launch window: Up to 24 hours unless a fresher verified backup exists
- Accepted RTO for first-client launch window: Up to 4 hours for controlled restore / rollback decision path
- Reason:
  - first launch is controlled and operator-assisted
  - customer count is minimal
  - safety and correctness are prioritized over fast-scale recovery promises

## 4. Rollback trigger

Rollback / NO-GO is triggered immediately if any of the following happens:

- Sev-1 outage with no safe workaround in launch window
- Kaspi day-1 required path fails and cannot be validated safely
- tenant safety / cross-tenant exposure risk appears
- billing/access control integrity is in doubt
- deployment succeeds partially but leaves platform in uncertain state
- restore path or latest backup identity cannot be confirmed when needed

## 5. Rollback authority

- Final rollback authority: Ақыл
- Operational executor: Ақыл
- Go/No-Go authority: Ақыл

## 6. Current evidence-based assessment

- Restore drill evidence exists: Yes
- Rollback path documented: Yes
- Latest concrete backup identifier confirmed for this launch packet: No
- Real launch-window backup verification attached in this packet: No
- Verdict: PARTIAL

## 7. Required before final GO

- Record latest available backup identifier for launch window
- Attach backup location/path used for launch candidate
- Confirm restore command/operator path is immediately available
- Keep rollback decision rule attached in launch packet

## 8. Final acceptance statement

- DR / rollback acceptance status: PARTIAL
- Launch may proceed only in controlled mode and only if rollback remains immediately available to the operator.
- If backup identity or rollback path cannot be confirmed at launch time, decision is automatically NO-GO.

## 9. Notes

- Existing repo evidence is strong enough to define rollback logic.
- Final launch still needs explicit backup identity confirmation.
- This acceptance is for first-client launch only, not long-term production maturity.
