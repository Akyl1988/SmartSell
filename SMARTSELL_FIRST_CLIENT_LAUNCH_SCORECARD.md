# SMARTSELL_FIRST_CLIENT_LAUNCH_SCORECARD

## 1) Executive launch status

**Status: CONDITIONAL PASS**

SmartSell is ready to onboard the first client only in controlled, operator-assisted mode, provided all mandatory blockers in Section 5 are closed and evidence is attached in this scorecard before go-live.

Date of assessment: 2026-03-14

---

## 2) Launch gate table

| Gate | Status | Evidence | Owner | Blocking? | Next action |
|---|---|---|---|---|---|
| Release evidence | Partial | `SMARTSELL_RELEASE_CHECKLIST.md` (all checkboxes currently unchecked); `SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md` (multiple PASS rehearsal cycles) | Founder/Ops | **Yes** | Complete one release gate run in `SMARTSELL_RELEASE_CHECKLIST.md` with explicit checkmarks + attached outputs (migration, smoke, health, rollback readiness). |
| Production deploy prerequisites | Fail | `PRODUCTION_DEPLOYMENT_CHECKLIST.md` (infrastructure/security/deploy items largely unchecked) | Founder/Ops | **Yes** | Mark minimum required prod prerequisites complete (env/secrets, TLS, DB/Redis, backup path, deploy command path), with operator sign-off. |
| Startup/readiness guards | Pass | `tests/test_health_and_ready.py`; `tests/test_core_startup_hook_guards.py`; `tests/test_process_role_gating.py`; `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md` | Backend | No | Keep strict readiness mode for production and preserve role split (`web` / `scheduler` / `runner`) in launch config. |
| Tenant isolation confidence | Pass | `tests/test_platform_admin_tenant_access_policy.py`; `tests/app/test_tenant_isolation_billing.py`; `tests/app/test_tenant_isolation_subscriptions.py`; `tests/app/api/test_preorders_rbac_tenant.py` | Backend | No | Re-run focused tenant isolation smoke in pre-launch window and archive output in release packet. |
| Billing/subscription confidence | Pass | `SMARTSELL_BILLING_STATE_MACHINE.md`; `SMARTSELL_BILLING_FAILURE_POLICY.md`; `tests/app/test_wallet_invariants.py`; `tests/app/api/test_subscriptions_api.py` | Product + Backend | No | Run one pre-launch billing recovery rehearsal for launch tenant (blocked -> recovered path) and store result. |
| Kaspi integration readiness | Partial | `SMARTSELL_LAUNCH_VERDICT.md`; `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md` (live Kaspi sanity attempts include `401` without auth context); `tests/app/api/test_kaspi_rbac.py`; `app/api/v1/kaspi_status_routes.py` | Backend | **Yes (if tenant depends on Kaspi at day-1)** | Execute authenticated tenant-level Kaspi sanity during launch window and attach result; if first client is non-Kaspi, mark as monitored non-blocking exception. |
| Onboarding rehearsal | Partial | `SMARTSELL_ONBOARDING_PLAYBOOK.md`; `SMARTSELL_ONBOARDING_DRY_RUN.md` (repeated simulated PASS); `SMARTSELL_EXECUTION_BOARD.md` marks onboarding as Partial | Founder/Ops | No (for first client) | Use playbook exactly once for first real client; produce full evidence pack and owner sign-off at activation. |
| Incident response readiness | Partial | `SMARTSELL_INCIDENT_PROCESS.md`; `SMARTSELL_EXECUTION_BOARD.md` marks incident process Partial | Founder/Ops | **Yes** | Assign named Incident Owner for launch week, prefill customer update templates, and run one timed incident comms drill before onboarding. |
| DR/restore readiness | Partial | `SMARTSELL_DR_RESTORE_DRILL.md` (restore cycles + timing evidence present; RPO/RTO still listed pending; Kaspi live sanity not confirmed) | Founder/Ops + Backend | **Yes** | Record accepted launch RPO/RTO values with measured evidence mapping; confirm rollback decision path for first client window. |
| Smoke/regression confidence | Pass | `test_results.txt` (`246 passed, 6 skipped`); 2026-03-14 focused suite in terminal context (`45 passed`) | Backend | No | Freeze critical paths and rerun same focused smoke set immediately pre-launch. |

Status scale used:
- **Pass** = evidence exists and is operationally usable for first-client launch.
- **Partial** = evidence exists but required operational closure or consistency is missing.
- **Fail** = required gate evidence is materially missing for first-client launch.

---

## 3) Contradictions / evidence gaps

1. `SMARTSELL_LAUNCH_VERDICT.md` says ready for first 10 in controlled mode, while `SMARTSELL_LAUNCH_CHECKLIST.md` remains entirely unchecked.
2. `SMARTSELL_EXECUTION_BOARD.md` marks several P0 tracks as `Exists`, but `SMARTSELL_RELEASE_CHECKLIST.md` is not visibly completed (all checklist boxes still unchecked).
3. `SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md` and `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md` contain strong rehearsal evidence, but this is not reconciled into pass-marked operator checklists.
4. `SMARTSELL_DR_RESTORE_DRILL.md` shows multiple restore cycles and timings, yet RPO/RTO sections still state pending achieved evidence.
5. Kaspi live sanity is repeatedly attempted in rehearsal docs but not confirmed in those specific runs (`401` without authenticated tenant context), creating an evidence-quality gap for Kaspi-dependent launch.

---

## 4) Mandatory blockers before first client

Only true blockers are listed:

1. **Release gate closure missing**
   - `SMARTSELL_RELEASE_CHECKLIST.md` must be completed for one concrete release candidate with linked outputs.

2. **Production deployment prerequisites not formally closed**
   - Minimum required controls in `PRODUCTION_DEPLOYMENT_CHECKLIST.md` must be checked and signed off (security, secrets, deploy path, health path, backup path).

3. **Launch-week incident operation not operationalized**
   - `SMARTSELL_INCIDENT_PROCESS.md` exists, but a named launch-week Incident Owner + timed communication drill + escalation path confirmation must be completed.

4. **DR gate not explicit for go/no-go**
   - Acceptable launch RPO/RTO and rollback trigger must be explicitly recorded for first-client window (not implied by scattered docs).

5. **Kaspi day-1 dependency unresolved (conditional blocker)**
   - If first client requires Kaspi immediately, authenticated tenant-level Kaspi sanity evidence is mandatory before onboarding.

---

## 5) Controlled launch plan

### Pre-launch (T-48h to T-1h)
- Freeze non-essential changes.
- Run and attach: migration check, focused smoke set, health/readiness checks, tenant isolation checks.
- Complete and sign: `SMARTSELL_RELEASE_CHECKLIST.md` + minimum required rows in `PRODUCTION_DEPLOYMENT_CHECKLIST.md`.
- Assign launch roles: Go/No-Go Owner, Incident Owner, Technical Operator.
- If Kaspi is day-1 required: run authenticated Kaspi sanity for the launch tenant.

### Launch window (T0)
- Execute onboarding strictly by `SMARTSELL_ONBOARDING_PLAYBOOK.md`.
- Collect full evidence pack during execution (auth, diagnostics, billing state snapshot, first core flow, integration check).
- Approve activation only if all mandatory blockers are closed and no Sev-1/Sev-2 unresolved condition exists.

### Post-launch monitoring (T+0 to T+24h)
- Active monitoring cadence with documented checkpoints (health, readiness, key tenant flow, billing state).
- Incident communication cadence follows `SMARTSELL_INCIDENT_PROCESS.md` if any degradation occurs.
- Archive launch evidence packet and final operator note.

### Rollback trigger
- Immediate rollback decision if any of the following occurs:
  - Sev-1 outage or multi-tenant impact without workaround,
  - first-client core flow failure not recoverable within launch window,
  - billing/access control failure risking incorrect tenant exposure,
  - required integration (Kaspi, if committed for day-1) fails without viable mitigation.

---

## 6) Final go/no-go rule

First-client onboarding may be authorized only by the **Go/No-Go Owner (Founder/Ops by default)** when all conditions below are simultaneously true:

1. All mandatory blockers in Section 4 are closed and marked with evidence links.
2. `SMARTSELL_RELEASE_CHECKLIST.md` is fully completed for the exact release candidate.
3. Minimum production prerequisites are marked complete in `PRODUCTION_DEPLOYMENT_CHECKLIST.md`.
4. Focused launch smoke/regression suite passes on current candidate.
5. Named Incident Owner and rollback authority are active for the launch window.
6. If client depends on Kaspi at launch, authenticated Kaspi sanity for that tenant is confirmed.

If any condition is not met, decision is automatically **NO-GO**.

---

## 7) Evidence ledger (source documents reconciled)

- `SMARTSELL_LAUNCH_VERDICT.md`
- `SMARTSELL_LAUNCH_CHECKLIST.md`
- `SMARTSELL_RELEASE_CHECKLIST.md`
- `PRODUCTION_DEPLOYMENT_CHECKLIST.md`
- `SMARTSELL_EXECUTION_BOARD.md`
- `SMARTSELL_ONBOARDING_PLAYBOOK.md`
- `SMARTSELL_ONBOARDING_DRY_RUN.md`
- `SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md`
- `SMARTSELL_RUNTIME_REHEARSAL_EVIDENCE.md`
- `SMARTSELL_INCIDENT_PROCESS.md`
- `SMARTSELL_DR_RESTORE_DRILL.md`
- `SMARTSELL_BILLING_FAILURE_POLICY.md`
- `SMARTSELL_BILLING_STATE_MACHINE.md`
- `test_results.txt`
