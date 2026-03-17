# LAUNCH_CANDIDATE_2026-03-14

## 1. Candidate identity

- Date: 2026-03-14
- Candidate branch: dev
- Candidate commit: $sha
- Prepared by: Ақыл
- Launch mode: controlled first-client onboarding

## 2. Freeze window

- Freeze start: 2026-03-14 10:00 Asia/Almaty
- Freeze end: 2026-03-21 18:00 Asia/Almaty
- Planned launch window: 2026-03-21 14:00-18:00 Asia/Almaty
- Non-essential changes: **forbidden during freeze**
- Allowed changes during freeze:
  - launch-blocker closure only
  - critical defect fixes only
  - evidence/runbook/checklist updates only

## 3. Launch ownership

- Go/No-Go Owner: Ақыл
- Incident Owner: Ақыл
- Backup Incident Owner: Ақыл
- Rollback Owner: Ақыл
- Technical Operator: Ақыл
- Customer communication owner: Ақыл

## 4. Launch scope

Planned first-client launch scope:

- Tenant creation / activation
- Admin access verification
- Billing/subscription activation
- Diagnostics verification
- Core flow verification
- Integration verification:
  - Kaspi day-1 required: Yes
  - If Yes: authenticated tenant-level Kaspi sanity required before GO

Out of scope for this launch:
- non-critical feature work
- additional refactor
- non-launch UX polish
- broad platform changes

## 5. Current blocker status from scorecard

### Blocker 1 — Release checklist closure
- Status: Open
- Evidence link:
- Owner: Ақыл

### Blocker 2 — Production deploy prerequisite subset
- Status: Open
- Evidence link:
- Owner: Ақыл

### Blocker 3 — Incident readiness operationalization
- Status: Open
- Evidence link:
- Owner: Ақыл

### Blocker 4 — DR / rollback acceptance
- Status: Open
- Evidence link:
- Owner: Ақыл

### Blocker 5 — Kaspi day-1 dependency
- Status: Open
- Evidence link:
- Owner: Ақыл

## 6. Required evidence packet for this candidate

The following evidence must be collected and linked before final GO:

- Release checklist completion
- Production prerequisite signoff
- Focused smoke/regression output
- Health/readiness output
- Tenant isolation checks
- Billing/subscription checks
- Incident drill record
- DR acceptance / rollback criteria
- Kaspi sanity evidence if required day-1

## 7. Planned execution timeline

- Day 1: candidate lock + ownership + freeze
- Day 2: release gate evidence run
- Day 3: production prerequisite closure
- Day 4: incident drill
- Day 5: DR / rollback acceptance
- Day 6: onboarding packet pre-assembly
- Day 7: final go/no-go meeting

## 8. Go / No-Go precondition

This candidate may proceed to first-client onboarding only if:

1. all mandatory blockers are closed,
2. evidence links are attached,
3. release checklist is complete,
4. rollback owner is assigned,
5. incident owner is assigned,
6. Kaspi sanity is confirmed because Kaspi is required for day-1.

Otherwise: **NO-GO**.

## 9. Notes

- Launch is operator-driven and controlled by a single owner.
- Kaspi is mandatory for launch value, so Kaspi sanity is a blocking requirement.
- No non-essential development work during freeze.
