# Minimal Production Deployment (Single VPS)

## 1. Purpose
- Minimal production-like deployment for the first 1-10 Kaspi merchants.
- Not a local dev setup; intended to serve real tenants with production safety guards enabled.

## Baseline
- Branch: main
- Tag: v0.2.2-owner-ui-stable

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
- Copy to .env.prod (used by docker-compose.prod.yml).
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
- If you set JWT_ACTIVE_KID in production, you must also set JWT_KEYS_<kid>_PRIVATE and JWT_KEYS_<kid>_PUBLIC
  (or *_PATH variants) so the KID material is present at startup.
- Explicit rule: SMARTSELL_KASPI_STUB (KASPI_STUB) must NEVER be enabled in production-like environments.
- Production readiness references:
  1) docs/PROD_READINESS_CHECKLIST.md
  2) docs/PROD_GATE.md

## 4. Deployment procedure
1) Clone the repository and checkout the desired tag/branch (example: v0.2.2-owner-ui-stable on main).
2) Prepare production environment:
   - Copy .env.example.prod to .env.prod and fill in required secrets.
   - Confirm PGCRYPTO is enabled in the database (docs/DEPLOYMENT.md).
3) Build and start with Docker Compose (docs/DEPLOYMENT.md, docs/runbooks/deploy_prod.md):
   - docker compose -f docker-compose.prod.yml up -d --build
4) Run database migrations (Alembic):
  - docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
5) Build frontend and serve via Nginx:
  - Run npm run build in the frontend workspace to produce dist/
  - Configure Nginx to serve dist/ and reverse-proxy /api to the backend (docs/DEPLOYMENT.md).

## 4A. Minimal production checklist (Linux, copy/paste)
1) Provision DB and Redis (Docker Compose on the VPS):
```bash
git checkout main
git pull
git checkout v0.2.2-owner-ui-stable

cp .env.example.prod .env.prod
# Edit .env.prod with real values and required secrets

docker compose -f docker-compose.prod.yml up -d --build
```
2) Apply migrations:
```bash
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```
3) Build frontend (set API base URL at build time):
```bash
cd frontend
VITE_API_URL=https://api.example.com npm run build
cd ..

sudo mkdir -p /var/www/smartsell
sudo rsync -a frontend/dist/ /var/www/smartsell/dist/
```
4) Serve frontend with Nginx (static + /api proxy):
```nginx
server {
   listen 80;
   server_name example.com;

   root /var/www/smartsell/dist;
   index index.html;

   location /api/ {
      proxy_pass http://127.0.0.1:8000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header X-Request-ID $request_id;
   }

   location / {
      try_files $uri /index.html;
   }
}
```
5) Health checks:
```bash
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8000/api/v1/wallet/health
```
6) Smoke scripts against prod (PowerShell on Linux requires pwsh):
```bash
export SMARTSELL_BASE_URL=https://api.example.com
export STORE_IDENTIFIER=store_admin@example.com
export STORE_PASSWORD='replace-me'

pwsh -NoProfile -File ./scripts/smoke-auth.ps1 -BaseUrl $SMARTSELL_BASE_URL -Identifier $STORE_IDENTIFIER -Password $STORE_PASSWORD
pwsh -NoProfile -File ./scripts/smoke-preorders-e2e.ps1 -BaseUrl $SMARTSELL_BASE_URL
pwsh -NoProfile -File ./scripts/smoke-repricing-e2e.ps1 -BaseUrl $SMARTSELL_BASE_URL
pwsh -NoProfile -File ./scripts/smoke-reports-wallet-transactions.ps1 -BaseUrl $SMARTSELL_BASE_URL
```

## 5. Post-deploy checks
- Health endpoints:
  1) GET /api/v1/health
  2) GET /ready
  3) GET /api/v1/wallet/health
- Smoke scripts (from scripts/):
  1) scripts/smoke-auth.ps1 (login + /me)
  2) scripts/smoke-preorders-e2e.ps1
  3) scripts/smoke-repricing-e2e.ps1
  4) scripts/smoke-reports-wallet-transactions.ps1
- Production gate reference:
  - docs/PROD_GATE.md and docs/PROD_READINESS_CHECKLIST.md

## 6. Handover to onboarding runbook
- Once the environment is healthy, follow:
  - docs/runbooks/first_kaspi_client_onboarding.md

## 7. Diagnostics per company (ops)
- Logs: filter by request_id (X-Request-ID) and search for company_id in API logs.
- Integration events (Kaspi): GET /api/v1/integrations/events?kind=kaspi&limit=100
- Tenant CSV reports (store admin or platform admin with companyId):
  - /api/v1/reports/preorders.csv
  - /api/v1/reports/inventory.csv
  - /api/v1/reports/repricing_runs.csv
  - /api/v1/reports/wallet/transactions.csv
  - /api/v1/reports/orders.csv
  - /api/v1/reports/order_items.csv

## 8. Scaling to ~10 clients (ops validation)
- One installation is expected to handle up to ~10 companies.
- Minimal validation procedure (no load testing):
  1) Create 2-3 companies (platform admin) and complete Kaspi connect for each.
  2) For each company, run:
     - Preorders flow (create -> confirm -> fulfill)
     - Repricing run (dry_run=true)
     - Wallet transactions CSV report
  3) Verify isolation:
     - /api/v1/auth/me returns the correct company_id per admin
     - CSV reports and /api/v1/repricing/runs show only the caller company data
  4) Confirm requests remain responsive and no cross-tenant data leakage occurs.

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
