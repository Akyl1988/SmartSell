# Status Report — 2026-02-03

## 1) Snapshot

- **Branch (current):** dev
- **Commit (dev):** 988ddc6
- **Commit (main):** 152bd91
- **Quality gate:** scripts/prod-gate.ps1
  - **Ruff:** ok
  - **Alembic smoke:** ok
  - **Pytest:** 488 passed, 6 skipped (0:05:20)
  - **Smoke:** OK (Kaspi sync-now skipped due to subscription_required)
  - **OpenAPI paths:** 235

## 2) Scope (sources of truth used)

- README.md
- docs/OBJECTIVE.md
- docs/CORE_PROD_PLAN.md
- docs/SMARTSELL_NEXT_PHASE_PLAN.md
- docs/PROD_GATE.md
- docs/PROD_READINESS_CHECKLIST.md
- docs/MIGRATIONS_POLICY.md
- docs/DEPLOYMENT.md
- docs/UPGRADE_PLAYBOOK.md
- docs/BACKUP_RESTORE.md
- docs/KASPI_FEED.md
- docs/KASPI_SYNC_RUNNER.md
- docs/KASPI_AUTOSYNC_MUTUAL_EXCLUSION.md
- docs/REGISTRATION_COMPANY_TENANT.md
- docs/TZ_COVERAGE_MATRIX.md
- PROJECT_JOURNAL.md

## 3) Feature matrix (MVP/Prod readiness)

| Module | MVP % | Prod % | Confidence % | Evidence / Notes |
| --- | --- | --- | --- | --- |
| Auth / Users / RBAC | 85 | 70 | 70 | Auth flows stabilized; error-contract enforced; smoke-auth covers logout/refresh; admin bootstrap without OTP documented. |
| Wallet / Billing / Subscriptions | 75 | 60 | 65 | Wallet invariants tests, payment intents contract, subscription enforcement skeleton + plan matrix; prod checklist flags remain. |
| Invoices (MVP core) | 70 | 55 | 60 | Invoices MVP core exists and tested (journal); production hardening pending. |
| Kaspi — Orders sync | 80 | 65 | 70 | Sync runner doc + tests, autosync mutual exclusion documented. |
| Kaspi — Orders list (D2) | 85 | 70 | 70 | DB-backed list + tenant tests present; paging/filtering in place. |
| Kaspi — Goods import (official) | 75 | 60 | 60 | Endpoints + tests; subscription gating applied. |
| Kaspi — offers.xml feed (public token) | 75 | 60 | 60 | Feed export + public token flow documented; tests exist. |
| Kaspi — Feed upload lifecycle | 80 | 65 | 65 | Job model + status/refresh/publish; tests and docs in KASPI_FEED. |
| Kaspi — sync/now orchestrator | 75 | 60 | 65 | Orchestrator present; gated by subscription; smoke handles 402 skip. |
| Migration policy + Alembic smoke | 90 | 80 | 75 | MIGRATIONS_POLICY + prod-gate alembic smoke passing. |
| Observability / logging / error contract | 80 | 70 | 65 | request_id contract enforced; integration events doc; structured logging. |
| Deployment / upgrade / backup-restore | 80 | 70 | 65 | DEPLOYMENT + UPGRADE_PLAYBOOK + BACKUP_RESTORE exist; runbooks not validated in prod. |
| Admin panel / notifications / analytics | 20 | 10 | 40 | Out of scope or partial per TZ_COVERAGE_MATRIX and CORE_PROD_PLAN. |

**Overall readiness:**
- **MVP:** 78%
- **Production:** 62%
- **Confidence:** 68%

## 4) MVP blockers (Top-10)

1. **Prod checklist P0:** SECRET_KEY/DB URL management via secrets manager and removal of weak defaults.
2. **Debug endpoint hardening:** protect or disable /api/v1/_debug/db in production.
3. **Dependency pinning / lockfile:** align FastAPI/Starlette/AnyIO/Trio and add lockfile (P0/P1).
4. **CI gate parity:** add CI steps for pip check + pytest + alembic dry-run (P1).
5. **Pydantic deprecation cleanup:** address config warnings (P1).
6. **Rate limiting and audit logging hardening:** verify security headers and rate limits (P2).
7. **Frontend parity:** React frontend lacks feature parity for core flows (TZ_COVERAGE_MATRIX).
8. **Payments gateway integration (TipTop Pay):** still TODO per TZ_COVERAGE_MATRIX.
9. **Analytics/Reports:** missing per TZ_COVERAGE_MATRIX.
10. **Operational SLOs/metrics:** observability metrics not defined; only logs/request_id.

## 5) Production blockers (Top-10)

1. **Secrets/rotation policy** and production environment enforcement (PROD_READINESS_CHECKLIST P0).
2. **Lockfile + dependency pinning** and upgrade path documentation (P0/P1).
3. **CI pipeline** for quality gates and alembic dry-run (P1).
4. **Debug/unsafe endpoints** locked down in prod (P0).
5. **Metrics/monitoring** beyond logs (P2).
6. **Security headers / rate limiting** verification (P2).
7. **Load testing + SLO definition** (P2).
8. **Payment provider integration** (TipTop Pay) (TZ_COVERAGE_MATRIX).
9. **Analytics/notifications** (TZ_COVERAGE_MATRIX).
10. **Frontend alignment** with backend (TZ_COVERAGE_MATRIX).

## 6) Next steps (5 tasks + DoD)

1. **Milestone B1 — Catalog Import v2 parsing**
   - **DoD:** CSV/XLSX parser abstraction; header normalization; tests on variants; prod-gate passes.
2. **Milestone B2 — Upsert + dry-run**
   - **DoD:** dry_run param; idempotent upsert; tests for duplicates and no-write mode; prod-gate passes.
3. **Milestone C2 — Feed upload lifecycle contract tests**
   - **DoD:** contract tests for upload/refresh/publish with request_id; clear error codes; prod-gate passes.
4. **Core Prod Plan F2 — Logging/error hardening**
   - **DoD:** request_id in logs everywhere; error contract in all endpoints; tests/grep verify; prod-gate passes.
5. **Prod Readiness P0 — Secrets/lockfile/CI**
   - **DoD:** lockfile committed; CI pipeline for pip check/pytest/alembic dry-run; secrets policy documented.
