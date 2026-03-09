# SMARTSELL_LAUNCH_VERDICT

## 1. Purpose
Provide an evidence-based launch-readiness verdict for SmartSell first-tenant rollout using only repository-grounded artifacts and execution evidence.

## 2. Current launch verdict
**Verdict: Ready for first 10 tenants in controlled, operator-assisted mode.**

This is **not** a verdict for hands-off scale.

## 3. What is already proven
- Core P0 launch tracks are documented and tracked on the execution board, with all listed P0 items currently in `Partial` state and evidence references present.
- Tenant diagnostics support surface exists and is validated (`GET /api/v1/admin/tenants/{company_id}/diagnostics`, tests present).
- Billing state model and failure/grace/suspension policy are defined and partially implemented/tested in code and tests.
- Incident process, release checklist, DR drill, onboarding playbook, and Kaspi support visibility contracts are documented.
- Simulated tenant run evidence exists:
  - onboarding dry run (`3 passed`) and
  - full tenant simulation (`7 passed`) covering auth/login, diagnostics, billing-state behavior, core preorder flow, Kaspi-related validation, report export path, and logout/session sanity.
- Frontend auth/session hardening has concrete implementation evidence for refresh-on-expiry retry behavior and successful frontend build.

### Frontend auth/session hardening verification (2026-03-09)
- Verified in code that session model no longer persists active session tokens in `localStorage`:
  - `frontend/src/auth/tokenStore.ts` keeps access token in memory and refresh token in `sessionStorage`, while removing legacy `localStorage` keys.
- Verified refresh retry is implemented as single-flight lock:
  - `frontend/src/api/client.ts` uses shared `refreshInFlight` promise and waits on it for concurrent requests.
- Verified logout/session reset path:
  - `frontend/src/hooks/useAuth.ts` clears session via `clearSessionTokens()` and redirects to `/auth/login`.
- Verified unauthorized/session-expired UX path:
  - `frontend/src/api/client.ts` dispatches `auth:unauthorized`; `frontend/src/hooks/useAuth.ts` handles it with redirect to `/auth/login?reason=session_expired`; `frontend/src/pages/Auth/LoginPage.tsx` shows clear user message.
- Build evidence:
  - Command: `npm --prefix frontend run build`
  - Output (key lines): `vite v4.5.14 building for production...`, `✓ 140 modules transformed.`, `✓ built in 995ms`.

## 4. What is still partially risky
- Most P0 tracks remain `Partial` rather than `Exists`.
- Release dry-run evidence is still largely pending (migration/smoke/post-deploy checks not fully evidenced).
- DR evidence confirms backup + DB restore, but full application-level restore verification is still pending.
- Runtime ownership split is documented but not yet repeatedly proven in real deployment records.
- Operational readiness still depends on disciplined operator execution and evidence collection, not yet on mature automation.

## 5. Accepted launch conditions
Launch first tenants only under these conditions:
- Operator-assisted onboarding and activation only.
- Strict use of onboarding playbook + evidence pack per tenant.
- Active use of incident process and diagnostics for support response.
- Controlled release gate using checklist with explicit go/no-go owner.
- No uncontrolled tenant growth beyond first-10 scope until repeated operational evidence improves.

## 6. What is NOT allowed during first-10 rollout
- No “hands-off” assumption for support, billing exceptions, or onboarding.
- No skipping release/rollback readiness checks for production changes.
- No scaling to larger client bands based on a single simulation run.
- No undocumented runtime role mixing (API vs worker/scheduler) as a permanent operating mode.

## 7. Recommended rollout mode
- **Mode:** Controlled, low-concurrency, operator-assisted rollout.
- **Cadence:** Stage tenants gradually, validate evidence pack after each activation.
- **Control:** Treat first-10 as a reliability program, not a growth sprint.

## 8. Final decision statement
SmartSell is **ready to onboard the first 10 tenants only in controlled/operator-assisted mode**, based on current repository evidence.

SmartSell is **not yet ready for hands-off scale**; additional repeated real-world operational evidence is required before moving beyond this rollout mode.
