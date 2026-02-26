# Ops Checklist - First Kaspi Client

## 1. Infrastructure & deployment
- [ ] Minimal production environment deployed per docs/DEPLOY_MINIMAL_PROD.md or docs/runbooks/deploy_prod.md.
- [ ] Database migrations applied (Alembic upgrade head).
- [ ] Health and readiness endpoints are OK (/api/v1/health and /ready).

## 2. Secrets & safety gates
- [ ] All critical env vars from docs/DEPLOY_MINIMAL_PROD.md are configured.
- [ ] Required-in-prod guards would pass (CSRF_SECRET, OTP_SECRET, PGCRYPTO_KEY, INVITE_TOKEN_SECRET, RESET_TOKEN_SECRET, etc.).
- [ ] KASPI_STUB is disabled in all production-like environments.

## 3. Functional smoke
- [ ] scripts/smoke-auth.ps1 passes (login + /me).
- [ ] scripts/smoke-preorders-e2e.ps1 passes.
- [ ] scripts/smoke-repricing-e2e.ps1 passes.
- [ ] scripts/smoke-reports-all.ps1 passes (or approved subset for the first client).

## 4. Onboarding readiness
- [ ] docs/runbooks/first_kaspi_client_onboarding.md reviewed and accepted.
- [ ] Test onboarding run completed on a non-production tenant (if applicable).

## 5. Client boundaries
- [ ] First Kaspi clients operate in read-only mode for Kaspi pricing and stock (no destructive apply actions) unless explicitly approved.
