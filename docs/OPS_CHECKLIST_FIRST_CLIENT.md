# Ops Checklist - First Kaspi Client

## Baseline
- Branch: main
- Tag: v0.2.2-owner-ui-stable

## 1. Infrastructure & deployment
- [ ] Minimal production environment deployed per docs/DEPLOY_MINIMAL_PROD.md or docs/runbooks/deploy_prod.md.
- [ ] Database migrations applied (Alembic upgrade head).
- [ ] Health and readiness endpoints are OK:
	- [ ] GET /api/v1/health
	- [ ] GET /ready
	- [ ] GET /api/v1/wallet/health

## 2. Secrets & safety gates
- [ ] All critical env vars from docs/DEPLOY_MINIMAL_PROD.md are configured.
- [ ] Required-in-prod guards would pass (CSRF_SECRET, OTP_SECRET, PGCRYPTO_KEY, INVITE_TOKEN_SECRET, RESET_TOKEN_SECRET, etc.).
- [ ] KASPI_STUB is disabled in all production-like environments (SMARTSELL_KASPI_STUB=0).
- [ ] If JWT_ACTIVE_KID is set in prod, JWT_KEYS_<kid>_PRIVATE and JWT_KEYS_<kid>_PUBLIC are present (or *_PATH variants).
- [ ] Prod guard tests that must remain true (implicit guarantees):
	- [ ] [tests/test_readiness_requires_secret_in_prod.py](tests/test_readiness_requires_secret_in_prod.py)
	- [ ] [tests/test_csrf_secret_required_in_prod.py](tests/test_csrf_secret_required_in_prod.py)
	- [ ] [tests/test_otp_secret_required_in_prod.py](tests/test_otp_secret_required_in_prod.py)
	- [ ] [tests/test_pgcrypto_key_required_in_prod.py](tests/test_pgcrypto_key_required_in_prod.py)
	- [ ] [tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py](tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py)
	- [ ] [tests/test_kaspi_stub_prod.py](tests/test_kaspi_stub_prod.py)
	- [ ] [tests/app/test_security_kid.py](tests/app/test_security_kid.py)

## 3. Functional smoke
- [ ] scripts/smoke-auth.ps1 passes (login + /me).
- [ ] scripts/smoke-preorders-e2e.ps1 passes.
- [ ] scripts/smoke-repricing-e2e.ps1 passes.
- [ ] scripts/smoke-reports-wallet-transactions.ps1 passes (if wallet is enabled).
- [ ] Base URL override and credentials are set for prod:
	- SMARTSELL_BASE_URL=https://api.example.com
	- STORE_IDENTIFIER / STORE_PASSWORD (store admin)
	- PLATFORM_IDENTIFIER / PLATFORM_PASSWORD (platform admin, if needed)

## 3A. Ops smoke (post-deploy, must be green)
- [ ] Auth smoke returns 200 and a valid /api/v1/auth/me payload.
- [ ] Preorders smoke creates/confirm/cancel/fulfill without 409/422 loops.
- [ ] Repricing smoke completes run/apply with dry_run=true.
- [ ] Wallet transactions CSV smoke returns a CSV header line.

## 4. Onboarding readiness
- [ ] docs/runbooks/first_kaspi_client_onboarding.md reviewed and accepted.
- [ ] Test onboarding run completed on a non-production tenant (if applicable).

## 5. Client boundaries
- [ ] First Kaspi clients operate in read-only mode for Kaspi pricing and stock (no destructive apply actions) unless explicitly approved.

## 6. Logging and monitoring basics
- [ ] API logs are reachable (Docker: docker compose -f docker-compose.prod.yml logs -f api).
- [ ] Kaspi integration events can be inspected: GET /api/v1/integrations/events?kind=kaspi&limit=100.
- [ ] Repricing errors are visible via GET /api/v1/repricing/runs (last_error, status) and in API logs.
- [ ] Typical failure clues:
	- Kaspi auth errors: 401/403 from /api/v1/kaspi/connect or /api/v1/kaspi/health/{store}
	- Repricing conflicts: 409 on run/apply (run already in progress)
	- Preorder failures: 409 INVALID_PREORDER_STATUS in confirm/cancel/fulfill flows

## 7. Multi-tenant sanity
- [ ] Two different store admins can log in and see different company_id in /api/v1/auth/me.
- [ ] Preorders are tenant-scoped:
	- Company A preorder list does not include Company B items.
	- Confirm/cancel/fulfill only affects the caller company.
- [ ] Repricing runs are tenant-scoped:
	- /api/v1/repricing/runs shows only the caller company runs.
	- /api/v1/reports/repricing_runs.csv exports only the caller company data.
- [ ] Wallet/report CSVs are tenant-scoped:
	- /api/v1/reports/wallet/transactions.csv
	- /api/v1/reports/preorders.csv
	- /api/v1/reports/inventory.csv
- [ ] Platform admin can see all companies, store admins cannot.
- [ ] Reference tenant isolation/RBAC tests (must remain green):
	- [tests/test_platform_admin_tenant_access_policy.py](tests/test_platform_admin_tenant_access_policy.py)
	- [tests/app/test_tenant_isolation.py](tests/app/test_tenant_isolation.py)
	- [tests/app/test_tenant_isolation_billing.py](tests/app/test_tenant_isolation_billing.py)
	- [tests/app/test_tenant_isolation_subscriptions.py](tests/app/test_tenant_isolation_subscriptions.py)
	- [tests/app/test_tenant_isolation_campaigns.py](tests/app/test_tenant_isolation_campaigns.py)
	- [tests/app/test_tenant_isolation_invoices.py](tests/app/test_tenant_isolation_invoices.py)
	- [tests/app/api/test_owner_admin_api.py](tests/app/api/test_owner_admin_api.py)

## 8. Company listing and per-tenant diagnostics
- [ ] List companies (platform admin): GET /api/v1/admin/companies and /api/v1/admin/companies/{company_id}.
- [ ] Subscriptions snapshot (platform admin): GET /api/v1/admin/subscriptions/stores.
- [ ] Reports with tenant isolation:
	- Store admin: call reports without companyId.
	- Platform admin: call reports with companyId query to target a company.
- [ ] For Kaspi issues per tenant, review integration events and filter logs by request_id or company_id.
