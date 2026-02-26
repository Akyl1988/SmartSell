# Minimal Production Deployment (Single VPS)

## 1. Purpose
- Minimal production-like deployment for the first 1-10 Kaspi merchants.
- Not a local dev setup; intended to serve real tenants with production safety guards enabled.

## 2. Architecture
- Single VPS (per PRODUCTION_DEPLOYMENT_CHECKLIST.md):
  1) CPU: 2+ cores (recommended 4+)
  2) RAM: 4GB+ (recommended 8GB+)
  3) Storage: 50GB+ SSD
  4) Network: 100Mbps+
- Components on the same host:
  1) SmartSell API backend (FastAPI app)
  2) PostgreSQL database (pgcrypto enabled)
  3) Redis
  4) Nginx (reverse proxy + serve frontend build)
- Recommended runtime: Docker Engine + Docker Compose (see docs/DEPLOYMENT.md and docs/runbooks/deploy_prod.md).

## 3. Environment and secrets
- Use .env.example.prod as a base and set production values (docs/runbooks/deploy_prod.md).
- Mandatory environment variables (docs/DEPLOYMENT.md, PROD_READINESS_CHECKLIST.md, and tests):
  1) ENVIRONMENT=production
  2) DEBUG=0/False
  3) SECRET_KEY (strong, unique)
  4) CSRF_SECRET (must differ from SECRET_KEY; required in prod)
  5) OTP_SECRET (required in prod; see otp_secret_required_in_prod)
  6) INVITE_TOKEN_SECRET (required in prod; invite_token_secret_required_in_prod)
  7) RESET_TOKEN_SECRET (required in prod; reset_token_secret_required_in_prod)
  8) PGCRYPTO_KEY (required in prod; pgcrypto_key_required_in_prod)
  9) INTEGRATIONS_MASTER_KEY (required for integrations secrets)
  10) DATABASE_URL (Postgres DSN)
  11) REDIS_URL (or REDIS_DISABLED=1 and REDIS_URL=disabled)
  12) PUBLIC_URL, ALLOWED_HOSTS, CORS_ORIGINS, BACKEND_CORS_ORIGINS
- Explicit rule: SMARTSELL_KASPI_STUB (KASPI_STUB) must NEVER be enabled in production-like environments.
- Production readiness references:
  1) docs/PROD_READINESS_CHECKLIST.md
  2) docs/PROD_GATE.md

## 4. Deployment procedure
1) Clone the repository and checkout the desired tag/branch (example: v0.2.0-frontend-mvp).
2) Prepare production environment:
   - Copy .env.example.prod to .env.prod and fill in required secrets.
   - Confirm PGCRYPTO is enabled in the database (docs/DEPLOYMENT.md).
3) Build and start with Docker Compose (docs/DEPLOYMENT.md, docs/runbooks/deploy_prod.md):
   - docker compose -f docker-compose.prod.yml up -d --build
4) Run database migrations (Alembic):
   - docker exec -it <api_container> alembic upgrade head
5) Build frontend and serve via Nginx:
   - Run npm run build in the frontend workspace to produce dist/
   - Configure Nginx to serve dist/ and reverse-proxy /api to the backend (docs/DEPLOYMENT.md).

## 5. Post-deploy checks
- Health endpoints:
  1) GET /api/v1/health
  2) GET /ready
  3) GET /api/v1/wallet/health
- Smoke scripts (from scripts/):
  1) scripts/smoke-auth.ps1 (login + /me)
  2) scripts/smoke-reports-all.ps1 (CSV/PDF reports)
- Production gate reference:
  - docs/PROD_GATE.md and docs/PROD_READINESS_CHECKLIST.md

## 6. Handover to onboarding runbook
- Once the environment is healthy, follow:
  - docs/runbooks/first_kaspi_client_onboarding.md

## Critical environment variables

| Name | Purpose | Enforced by |
| --- | --- | --- |
| ENVIRONMENT | Enables production guards and strict startup checks. | [app/main.py](app/main.py), [docs/PROD_GATE.md](docs/PROD_GATE.md) |
| SECRET_KEY | JWT signing and core crypto secret. | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| CSRF_SECRET | CSRF signing secret; must differ from SECRET_KEY in prod. | [tests/test_csrf_secret_required_in_prod.py](tests/test_csrf_secret_required_in_prod.py), [app/core/security.py](app/core/security.py) |
| OTP_SECRET | OTP HMAC secret for auth flows. | [tests/test_otp_secret_required_in_prod.py](tests/test_otp_secret_required_in_prod.py), [app/models/otp.py](app/models/otp.py) |
| INVITE_TOKEN_SECRET | Dedicated secret for invitation tokens. | [tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py](tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py), [app/utils/tokens.py](app/utils/tokens.py) |
| RESET_TOKEN_SECRET | Dedicated secret for password reset tokens. | [tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py](tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py), [app/utils/tokens.py](app/utils/tokens.py) |
| PGCRYPTO_KEY | DB encryption key for pgcrypto-backed fields. | [tests/test_pgcrypto_key_required_in_prod.py](tests/test_pgcrypto_key_required_in_prod.py), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| INTEGRATIONS_MASTER_KEY | Master key for integrations secrets. | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| DATABASE_URL | Postgres connection string (required in prod). | [app/core/db.py](app/core/db.py), [app/main.py](app/main.py) |
| REDIS_URL | Redis connection string (or disable Redis explicitly). | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |

- KASPI_STUB must be disabled in any production-like environment (see [tests/test_kaspi_stub_prod.py](tests/test_kaspi_stub_prod.py)).
