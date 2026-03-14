# SMARTSELL_FIRST_CLIENT_LAUNCH_CLOSURE_PLAN

Date: 2026-03-14
Scope: First-client launch closure from current `CONDITIONAL PASS` scorecard state.

Source baseline:
- `SMARTSELL_FIRST_CLIENT_LAUNCH_SCORECARD.md`
- `SMARTSELL_RELEASE_CHECKLIST.md`
- `PRODUCTION_DEPLOYMENT_CHECKLIST.md`
- `SMARTSELL_ONBOARDING_PLAYBOOK.md`
- `SMARTSELL_ONBOARDING_DRY_RUN.md`
- `SMARTSELL_INCIDENT_PROCESS.md`
- `SMARTSELL_DR_RESTORE_DRILL.md`
- `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md`
- `SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md`

---

## 1) Ranked blocker-closure plan

### Blocker 1 — Release gate closure is not completed in operator checklist
- Exact objective:
  - Close one concrete release candidate in `SMARTSELL_RELEASE_CHECKLIST.md` with explicit checkmarks and linked evidence outputs.
- Owner:
  - Founder/Ops (primary), Backend (evidence support).
- Evidence required:
  - Candidate version/commit stamp.
  - Migration pass output.
  - Focused smoke pass output.
  - Health/readiness confirmation (`/api/v1/health`, `/ready`, wallet/worker check).
  - Rollback readiness confirmation and artifact presence (`tmp/drill/smartsell_main_drill.sql`).
- Completion condition:
  - All pre-release and post-deploy checks marked complete in `SMARTSELL_RELEASE_CHECKLIST.md` for the selected candidate; links to evidence packet attached.
- Can it be done without code changes?
  - **Yes.**

### Blocker 2 — Production deploy prerequisites not formally closed
- Exact objective:
  - Close minimum mandatory deployment controls in `PRODUCTION_DEPLOYMENT_CHECKLIST.md` (not entire long-form list).
- Owner:
  - Founder/Ops.
- Evidence required:
  - Environment/security proof (`ENVIRONMENT=production`, `DEBUG=0`, host/CORS restrictions, secrets set).
  - DB/Redis connectivity outputs.
  - TLS/reverse proxy verification output.
  - Backup path confirmation and latest backup identifier.
  - Deploy command path confirmation (`docker compose ...` / migration command outputs).
- Completion condition:
  - A clearly marked “minimum first-client subset” in `PRODUCTION_DEPLOYMENT_CHECKLIST.md` is checked and signed by owner.
- Can it be done without code changes?
  - **Yes.**

### Blocker 3 — Incident operation is documented but not launch-operationalized
- Exact objective:
  - Operationalize `SMARTSELL_INCIDENT_PROCESS.md` for launch week with named owner, escalation contacts, and one timed drill.
- Owner:
  - Founder/Ops.
- Evidence required:
  - Named Incident Owner + backup owner.
  - Completed timed incident drill record using internal + customer templates.
  - SLA acknowledgement timing log (Sev-1/Sev-2 target adherence).
- Completion condition:
  - One completed launch-week incident drill log archived and linked from launch packet; owner assignment published.
- Can it be done without code changes?
  - **Yes.**

### Blocker 4 — DR go/no-go gate is implicit, not explicit
- Exact objective:
  - Convert DR evidence into explicit launch decision criteria (accepted RPO/RTO, rollback trigger, rollback owner).
- Owner:
  - Founder/Ops + Backend.
- Evidence required:
  - Existing restore cycle logs (`dr_cycle4`, `dr_cycle5`) mapped to measured duration.
  - Declared accepted launch RPO/RTO values and rationale.
  - Rollback owner and trigger conditions documented in meeting template.
- Completion condition:
  - DR section updated with explicit launch acceptance statement and referenced in go/no-go packet.
- Can it be done without code changes?
  - **Yes.**

### Blocker 5 (Conditional) — Kaspi day-1 dependency unresolved
- Exact objective:
  - Resolve whether first client is Kaspi day-1 dependent; if yes, produce authenticated tenant-level Kaspi sanity evidence.
- Owner:
  - Founder/Ops (dependency decision), Backend (execution support).
- Evidence required:
  - Dependency decision record: “Kaspi required day-1: Yes/No”.
  - If Yes: authenticated run output for tenant-level Kaspi status/sync health.
  - If No: explicit non-blocking exception note with monitoring plan.
- Completion condition:
  - Either authenticated Kaspi sanity PASS attached, or signed exception that first client is non-Kaspi at launch.
- Can it be done without code changes?
  - **Usually yes.**
  - **Escalate to engineering only if authenticated sanity fails due product defect.**

---

## 2) 7-day execution table

| Day | Task | Owner | Input docs | Output artifact | Pass condition |
|---|---|---|---|---|---|
| Day 1 | Lock launch candidate and freeze non-essential changes | Founder/Ops | `SMARTSELL_FIRST_CLIENT_LAUNCH_SCORECARD.md`, `SMARTSELL_RELEASE_CHECKLIST.md` | `docs/launch/LAUNCH_CANDIDATE_YYYY-MM-DD.md` | Candidate commit/version fixed; freeze window announced; owners assigned (Go/No-Go, Incident, Rollback). |
| Day 2 | Close release checklist with fresh evidence run | Founder/Ops + Backend | `SMARTSELL_RELEASE_CHECKLIST.md`, `SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md`, `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md` | `docs/launch/release_gate_run_YYYY-MM-DD.md` + terminal outputs | All checklist rows for chosen candidate checked; migration/smoke/health/rollback evidence attached. |
| Day 3 | Close minimum production deploy prerequisite subset | Founder/Ops | `PRODUCTION_DEPLOYMENT_CHECKLIST.md` | `docs/launch/prod_prereq_signoff_YYYY-MM-DD.md` | Minimum first-client subset complete and signed (secrets, DB/Redis, TLS/proxy, backup path, deploy path). |
| Day 4 | Incident launch-week drill and comms rehearsal | Founder/Ops | `SMARTSELL_INCIDENT_PROCESS.md` | `docs/launch/incident_drill_YYYY-MM-DD.md` | Timed drill completed; internal + customer templates filled; SLA timing meets targets; incident owner confirmed. |
| Day 5 | DR closure and rollback authority finalization | Founder/Ops + Backend | `SMARTSELL_DR_RESTORE_DRILL.md`, `SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md` | `docs/launch/dr_acceptance_YYYY-MM-DD.md` | Accepted RPO/RTO explicitly recorded; rollback trigger and owner documented; restore evidence linked. |
| Day 6 | Onboarding rehearsal-to-live bridge and packet pre-assembly | Founder/Ops + Backend | `SMARTSELL_ONBOARDING_PLAYBOOK.md`, `SMARTSELL_ONBOARDING_DRY_RUN.md` | `docs/launch/onboarding_packet_template_YYYY-MM-DD.md` | First-client onboarding packet template complete with required fields + evidence placeholders; day-1 script finalized. |
| Day 7 | Final go/no-go meeting and first-client activation decision | Go/No-Go Owner (Founder/Ops) | All docs above + `SMARTSELL_FIRST_CLIENT_LAUNCH_SCORECARD.md` | `docs/launch/go_no_go_minutes_YYYY-MM-DD.md` | Decision is GO only if all blockers closed with links; otherwise NO-GO with next closure date. |

---

## 3) Launch evidence packet checklist

Archive location (recommended): `docs/launch/evidence/<candidate_version_or_date>/`

### A. Candidate identity and freeze
- [ ] Candidate branch + commit hash output (`git rev-parse --abbrev-ref HEAD`, `git rev-parse --short HEAD`).
- [ ] Timestamp of release window start/end.
- [ ] Freeze announcement note.

### B. Release gate evidence
- [ ] Migration test output (`test_alembic_upgrade_head_runs`).
- [ ] Focused smoke output (auth + diagnostics + tenant flow + role gating).
- [ ] Health/readiness output (`/api/v1/health`, `/ready`, wallet/worker health if used).
- [ ] Rollback readiness output (`test_upgrade_playbook_docs...`, backup artifact presence).

### C. Production prerequisite evidence
- [ ] Redacted environment checklist proof (required variables configured, no secret values exposed).
- [ ] DB connectivity output.
- [ ] Redis connectivity output.
- [ ] TLS/proxy verification output.
- [ ] Deploy command output excerpt (`docker compose ...`, migration apply command).

### D. Incident readiness evidence
- [ ] Named Incident Owner + backup owner record.
- [ ] Incident drill log with timestamps and status transitions.
- [ ] Internal update template sample filled.
- [ ] Customer update template sample filled.

### E. DR and rollback evidence
- [ ] Restore logs (`tmp/drill/dr_cycle4_restore.log`, `tmp/drill/dr_cycle5_restore.log` or latest equivalent).
- [ ] Restore table check output (`\dt` output / log).
- [ ] Post-restore verification test output.
- [ ] Explicit accepted RPO/RTO statement for first-client window.
- [ ] Rollback owner + trigger matrix.

### F. Onboarding execution evidence (first client)
- [ ] Tenant ID, activation timestamp, onboarding owner.
- [ ] Admin login verification proof.
- [ ] Billing/subscription state snapshot.
- [ ] Diagnostics snapshot (`/api/v1/admin/tenants/{company_id}/diagnostics`).
- [ ] First core flow proof (request/response or test output).
- [ ] Integration sanity proof (Kaspi if required day-1).
- [ ] Rollback note (`used` / `not used`) and reason.

### G. Final decision evidence
- [ ] Completed go/no-go meeting note.
- [ ] Decision (`GO` / `NO-GO`) with signer.
- [ ] Blocker status table with links.

---

## 4) Go/no-go meeting template (operator)

```markdown
# SmartSell First-Client Go/No-Go

Date/Time:
Candidate version/commit:
Go/No-Go Owner:

## Blockers status
- Release checklist closure: PASS / FAIL (evidence link)
- Production prereq closure: PASS / FAIL (evidence link)
- Incident drill + owner: PASS / FAIL (evidence link)
- DR acceptance (RPO/RTO + rollback): PASS / FAIL (evidence link)
- Kaspi day-1 condition: PASS / N/A / FAIL (evidence link)

## Evidence links
- Release gate packet:
- Production prereq sign-off:
- Incident drill log:
- DR acceptance:
- Onboarding packet:

## Decision
- GO / NO-GO:
- Rationale:

## Ownership
- Rollback owner:
- Incident owner:

## If NO-GO
- Remaining blockers:
- Next review date/time:
```

---

## 5) Strict recommendation

**Recommendation: onboard first client after blocker closure.**

Current state is still `CONDITIONAL PASS`. Do not onboard until Blockers 1–4 are closed and Blocker 5 is resolved (either Kaspi PASS evidence if required day-1, or signed non-Kaspi exception).
