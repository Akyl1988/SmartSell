# SMARTSELL_RELEASE_CHECKLIST

## 1. Purpose
Define a lightweight but strict production release gate for SmartSell so first-client releases are predictable, verifiable, and reversible.

## 2. Pre-release checks
- [ ] **Database migrations**: migration order validated; upgrade runs cleanly on staging/prod-like DB.
- [ ] **Secrets present**: required env vars configured (DB, JWT, Redis, Kaspi, billing-related keys).
- [ ] **Redis connectivity**: app can connect and ping Redis from release environment.
- [ ] **Kaspi integration health**: token/session valid; last sync path callable; no unresolved critical integration error.
- [ ] **Billing subsystem health**: subscription checks and renewal path are operational; no blocking billing errors in logs.
- [ ] **Smoke tests passing**: targeted smoke scripts/tests pass for core flows (auth, orders, inventory/reservations, critical admin checks).

## 3. Deployment steps
1. Announce release window and freeze non-essential changes.
2. Backup database / ensure recent recoverable snapshot exists.
3. Deploy application artifact/container.
4. Run DB migrations.
5. Restart API/worker processes in controlled order.
6. Confirm service startup health checks.

## 4. Post-deploy verification
- [ ] API health endpoint(s) return success.
- [ ] Authentication/login works for admin role.
- [ ] One tenant read/write sanity check succeeds (no cross-tenant leakage).
- [ ] Kaspi sync trigger/list path responds without critical errors.
- [ ] Billing/subscription read path works for active tenant.
- [ ] Key background worker/scheduler loop is alive.

## 5. Rollback procedure
1. Declare rollback decision owner (Founder/Ops by default).
2. Stop current rollout and switch traffic/processes to last known good version.
3. If migration is backward-incompatible, execute documented DB restore/rollback plan.
4. Validate core health endpoints and tenant login flow.
5. Communicate rollback status internally and to affected customers (if impact existed).
6. Open incident record if customer impact exceeded release window.

## 6. Evidence required before considering release successful
- Release timestamp and deployed version/commit.
- Migration command output (success).
- Smoke test output (pass).
- Health check confirmation (API + Redis + worker).
- One-line tenant sanity check result.
- Any incident/rollback notes (or explicit “none”).

## 7. Exit criteria for marking Release Checklist as Partial/Exists

### Partial
- Checklist document exists and is used for at least one real release.
- Pre-release, deploy, and post-deploy sections are completed with basic evidence.

### Exists
- Checklist is used consistently for all production releases.
- Evidence set is archived for at least two successful releases.
- Rollback procedure has been validated at least once (real event or controlled drill).
