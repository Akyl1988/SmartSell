# Add a New Company (N-th Client Runbook)

## Baseline
- Branch: main
- Tag: v0.2.2-owner-ui-stable

## Roles
- Platform admin (owner cabinet + /api/v1/admin/*).
- Store admin (tenant user; uses /api/v1/* tenant endpoints and the storefront UI).

## 1) Collect required inputs
- Company name and BIN/IIN.
- Store admin phone or email for the invite.
- Kaspi merchant UID and API token (per store).
- Decide initial plan (trial/pro) and trial days if needed.

## 2) Create company (platform admin)
Platform admin only.

API:
```bash
curl -X POST https://api.example.com/api/v1/admin/companies \
  -H "Authorization: Bearer <platform_admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme Store","bin_iin":"123456789012"}'
```
Expected OK: 200 with company id and name.

UI waypoint:
- Owner -> Companies (list) should show the new company.

## 3) Create store admin invite (platform admin)
Platform admin only.

API:
```bash
curl -X POST https://api.example.com/api/v1/admin/invites \
  -H "Authorization: Bearer <platform_admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"company_id":123,"phone":"77001234567","grace_days":7,"initial_plan":"trial_pro"}'
```
Expected OK: 200 with invite_url.

UI waypoint:
- Owner -> Company detail -> Create admin invite.

Store admin action:
- Open invite_url in the browser and complete account setup.

## 4) Assign or extend a plan (platform admin)
Platform admin only.

Options:
- Grant trial: POST /api/v1/admin/subscriptions/trial
- Activate from wallet: POST /api/v1/admin/subscriptions/activate
- Set plan directly: POST /api/v1/admin/subscriptions/{company_id}/set-plan
- Extend plan: POST /api/v1/admin/subscriptions/{company_id}/extend
- Kaspi trial override (per merchant UID): POST /api/v1/admin/subscriptions/trial/kaspi

UI waypoint:
- Owner -> Subscriptions (change plan or extend).
- Owner -> Companies (Kaspi trial modal).

## 5) Connect Kaspi (store admin)
Store admin only.

API:
- POST /api/v1/kaspi/connect (company_name, store_name, token, verify=true)
- GET /api/v1/kaspi/health/{store}
- GET /api/v1/kaspi/status

Expected OK:
- /connect returns connected=true.
- /health and /status return 200 without kaspi auth errors.

## 6) Verify products and preorders (store admin)
Store admin only.

- Products page: confirm SKU/price/stock data.
- Enable preorders for a product if needed:
  - PUT /api/v1/products/{product_id} with is_preorder_enabled, preorder_lead_days, preorder_deposit.
- Preorders page: confirm list and status transitions.

Smoke (per company):
```bash
export SMARTSELL_BASE_URL=https://api.example.com
export STORE_IDENTIFIER=store_admin@example.com
export STORE_PASSWORD='replace-me'

pwsh -NoProfile -File ./scripts/smoke-preorders-e2e.ps1 -BaseUrl $SMARTSELL_BASE_URL
```

## 7) Verify repricing (store admin)
Store admin only.

- UI: Repricing page -> Run repricing.
- API: POST /api/v1/repricing/run
- Check runs: GET /api/v1/repricing/runs
- CSV: GET /api/v1/reports/repricing_runs.csv

Smoke (per company):
```bash
pwsh -NoProfile -File ./scripts/smoke-repricing-e2e.ps1 -BaseUrl $SMARTSELL_BASE_URL
```

## 8) Verify wallet and reports (store admin)
Store admin only.

- Wallet: GET /api/v1/wallet/accounts and /api/v1/wallet/accounts/{account_id}/balance
- Reports:
  - /api/v1/reports/preorders.csv
  - /api/v1/reports/inventory.csv
  - /api/v1/reports/repricing_runs.csv
  - /api/v1/reports/wallet/transactions.csv
  - /api/v1/reports/orders.csv
  - /api/v1/reports/order_items.csv

Smoke (per company):
```bash
pwsh -NoProfile -File ./scripts/smoke-reports-wallet-transactions.ps1 -BaseUrl $SMARTSELL_BASE_URL
```

## 9) Multi-tenant sanity (platform admin)
Platform admin only.

- Log in as Company A admin and Company B admin; confirm /api/v1/auth/me returns different company_id values.
- Run the preorders and repricing smoke scripts under each admin account; results must stay tenant-scoped.
- Use CSV reports per tenant and confirm no cross-tenant data:
  - /api/v1/reports/preorders.csv
  - /api/v1/reports/repricing_runs.csv
  - /api/v1/reports/wallet/transactions.csv

## 10) Diagnostics per company
- Integration events: GET /api/v1/integrations/events?kind=kaspi&limit=100
- Logs: filter for company_id or request_id; use X-Request-ID in client requests to trace.
- Admin overview: GET /api/v1/admin/companies and /api/v1/admin/companies/{company_id}

## Dev-песочница
- Для dev можно быстро создать вторую/третью тестовую компанию и store_admin:
  - scripts/dev-create-sandbox-tenant.ps1
- Требуемые env:
  - ADMIN_IDENTIFIER / ADMIN_PASSWORD (platform admin)
  - SANDBOX_STORE2_PASSWORD (пароль для нового store_admin)
- Если нет platform_admin, сначала выдайте роль в dev:
  - python ./scripts/dev-bootstrap-platform-admin.py --identifier <phone_or_email>
  - Опционально: --superuser
- Проверка доступа:
  - POST /api/v1/auth/login (identifier/password)
  - GET /api/v1/auth/me
  - GET /api/v1/admin/companies (должно быть 200)
- Пароль для store_admin должен соответствовать политике (>=8 символов, минимум одна буква).
- Что делает скрипт:
  - логинится под platform admin
  - создаёт company или переиспользует по BIN/IIN
  - создаёт invite
  - принимает invite и проверяет /api/v1/auth/me (при 4xx/5xx выводит status+body)
  - upsert записи sandbox-store-2 в scripts/smoke-tenants.json
- Пример запуска:
  - pwsh -NoProfile -File ./scripts/dev-create-sandbox-tenant.ps1 -BaseUrl http://127.0.0.1:8000
- Пример содержимого scripts/smoke-tenants.json:
```json
[
  {
    "label": "sandbox-store-2",
    "identifier": "77001234567",
    "passwordEnv": "SANDBOX_STORE2_PASSWORD"
  }
]
```
- Мульти-тенант smoke:
  - pwsh -NoProfile -File ./scripts/smoke-multi-tenant-e2e.ps1 -BaseUrl http://127.0.0.1:8000
  - Зелёный результат: GLOBAL: OK (all tenants passed) или GLOBAL: OK (some tenants failed).
