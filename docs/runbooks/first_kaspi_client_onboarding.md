# First Kaspi Client Onboarding (Runbook)

## 1. Purpose
- Success means the first real Kaspi store can log in and see their own data in the UI:
   1) Products list populated from Kaspi-backed catalog
   2) Preorders visible and actions (confirm/cancel/fulfill) work end-to-end
   3) Repricing runs visible after a manual run
   4) Wallet balance visible
   5) CSV reports download correctly (preorders, inventory, repricing runs, wallet transactions, orders, order items)
- Read-only policy (mandatory for first real clients): no repricing apply and no destructive Kaspi price/stock changes unless explicitly approved.

## Baseline
- Branch: main
- Tag: v0.2.2-owner-ui-stable

## 2. Prerequisites
- SmartSell is running end-to-end:
  1) Backend API
  2) PostgreSQL (pgcrypto enabled)
  3) Redis (optional, but recommended in production)
  4) Frontend
- Admin or platform-admin account is available (required for subscription overrides, wallet topups, and global operations).
- Production safety guards are satisfied (tests enforce these):
   1) SECRET_KEY and dedicated token secrets set (see tests/test_invite_reset_tokens_require_dedicated_secrets_in_prod.py)
   2) CSRF secret is set (tests/test_csrf_secret_required_in_prod.py)
   3) OTP secret is set (tests/test_otp_secret_required_in_prod.py)
   4) PGCRYPTO_KEY is set (tests/test_pgcrypto_key_required_in_prod.py)
   5) If JWT_ACTIVE_KID is set, JWT_KEYS_<kid>_PRIVATE and JWT_KEYS_<kid>_PUBLIC are provided (tests/app/test_security_kid.py)
   6) KASPI_STUB is disabled in production-like environments (tests/test_kaspi_stub_prod.py)
- Explicit rule: KASPI_STUB must NEVER be enabled in any production-like environment.
- Reference deployment checks:
   1) docs/DEPLOYMENT.md (required env vars, kaspi onboarding, health checks)
   2) docs/PROD_GATE.md and docs/PROD_READINESS_CHECKLIST.md

## 3. Data to request from the client
- Company details:
  1) Legal company name
  2) BIN/IIN
- Kaspi store identifiers and credentials:
  1) Merchant UID / store identifier
  2) Kaspi API token (for connect/selftest)
- Operating constraints:
  1) Read-only mode or permission to apply changes (pricing/stock)
  2) Allowed test actions in production (e.g., preorder confirm/cancel/fulfill)

## 4. Creating company and admin user
1) Create the company + initial admin user:
   - Use POST /api/v1/auth/register for the first store admin (or your existing bootstrap flow).
   - Use scripts/smoke-auth.ps1 to validate login + /api/v1/auth/me.
2) Verify login works:
   - POST /api/v1/auth/login
   - GET /api/v1/auth/me
   - Confirm company_id, company_name, and role from /me.
3) Store the credentials securely and confirm the admin can sign in to the UI.
4) UI waypoint (Owner):
   - Owner -> Companies: confirm the company record and invite link if needed.

## 5. Connecting Kaspi
1) Connect the tenant to Kaspi:
   - POST /api/v1/kaspi/connect (company_name, store_name, token, verify=true).
   - Reference: docs/KASPI_FEED.md (connect section).
2) Verify connectivity:
   - GET /api/v1/kaspi/health/{store}
   - GET /api/v1/kaspi/status
   - Optional: GET /api/v1/kaspi/_debug/ping (non-prod only)
3) UI waypoint:
   - Kaspi feed control page (UI) is a debug panel for ping/health and feed actions.
   - Use the API endpoints above for production validation.
4) Ensure KASPI_STUB is disabled for production-like runs (must never be enabled).
5) Check integration events for errors:
   - GET /api/v1/integrations/events?kind=kaspi&limit=100

## 6. Initial full synchronization
1) Orders sync (manual trigger):
   - POST /api/v1/kaspi/orders/sync (script resolves path via openapi.json)
   - Use scripts/smoke-kaspi-sync-now.ps1 (platform admin + KASPI_MERCHANT_UID).
2) Catalog / products sync (Kaspi constraints):
   - Use the goods import and offers feed pipelines described in docs/KASPI_SYNC_RUNNER.md and docs/KASPI_FEED.md.
   - Typical goods import sequence:
     1) POST /api/v1/kaspi/goods/import (payload or product_ids)
     2) GET /api/v1/kaspi/goods/import/status?importCode=<code>
     3) GET /api/v1/kaspi/goods/import/<code>/result
   - File upload path (if using Excel/CSV export):
     1) POST /api/v1/kaspi/goods/import/upload (multipart file)
     2) GET /api/v1/kaspi/goods/import/status?importCode=<code>
     3) GET /api/v1/kaspi/goods/import/<code>/result
3) If using the public price list flow:
   - POST /api/v1/kaspi/offers/feed/upload to obtain a public URL
   - Configure the URL in the Kaspi seller cabinet
4) If using internal feed export + upload:
   - POST /api/v1/kaspi/feeds/products/generate -> capture export_id
   - POST /api/v1/kaspi/feeds/{export_id}/upload?merchantUid=<merchant_uid>
   - Optional: GET /api/v1/kaspi/feeds/{export_id}/payload (inspect XML)
5) Quick XML feed check:
   - GET /api/v1/kaspi/feed (current company XML feed)
6) Expected duration:
   - Initial runs can take minutes; watch logs for kaspi_import and sync status.
7) Verification:
   - Confirm API responses are 2xx and status fields move to success/done
   - Use existing tests as behavior references:
     - tests/test_kaspi_orders_sync_runner.py
     - tests/app/test_kaspi_orders_list_d2.py
     - tests/app/test_kaspi_products_sync.py
     - tests/app/test_kaspi_import_poll_runner.py

## 7. Verifying Products and Preorders in the UI
1) Log into the frontend as the client admin.
2) Products page:
   - Confirm real items are listed (no mocks).
   - Verify SKU, price, stock, updated_at render correctly.
3) Preorders page:
   - If no real preorders exist, create a safe test preorder via POST /api/v1/preorders.
   - Use scripts/smoke-preorders-e2e.ps1 for the end-to-end flow (product, stock, preorder, confirm, cancel, fulfill).
   - Expected statuses: new, confirmed, fulfilled, cancelled.
   - To enable preorders for a product:
     - PUT /api/v1/products/{product_id} with is_preorder_enabled, preorder_lead_days, preorder_deposit, preorder_note.
4) Safety note:
   - Do not touch real production orders unless explicitly approved by the client.
   - If in read-only mode, avoid confirm/cancel/fulfill on real orders.

## 8. Verifying Repricing
1) Enable Pro access for the tenant (choose one path):
   - Admin subscription trial: POST /api/v1/admin/subscriptions/trial
   - Admin subscription activate: POST /api/v1/admin/subscriptions/activate
   - Kaspi subscription override: PUT /api/v1/admin/subscription-overrides/kaspi/{merchant_uid}
   - Wallet top-up if required: POST /api/v1/admin/wallet/topup
2) Run repricing once:
   - From UI: Repricing page -> Run repricing
   - From API: POST /api/v1/repricing/run
   - Optional test flow: scripts/smoke-repricing-e2e.ps1
3) Verify repricing runs list:
   - GET /api/v1/repricing/runs
   - Confirm status, timestamps, and last_error fields
4) Export runs as CSV:
   - Reports page: Download Repricing Runs CSV
   - API: GET /api/v1/reports/repricing_runs.csv
5) Read-only note:
   - Do not apply prices to Kaspi unless explicitly approved.
   - Avoid POST /api/v1/repricing/runs/{run_id}/apply in read-only onboarding.

## 9. Verifying Wallet and Reports
1) Wallet API checks:
   - GET /api/v1/wallet/accounts (identify the tenant account)
   - GET /api/v1/wallet/accounts/{account_id}/balance
   - UI Wallet page should show balance and currency.
2) Reports (CSV):
   - /api/v1/reports/preorders.csv
   - /api/v1/reports/inventory.csv
   - /api/v1/reports/repricing_runs.csv
   - /api/v1/reports/wallet/transactions.csv
   - /api/v1/reports/orders.csv
   - /api/v1/reports/order_items.csv
   - Use scripts/smoke-reports-wallet-transactions.ps1 as the primary smoke for the first client.
3) Quick validation on CSVs:
   - Tenant isolation (only the client data)
   - Non-empty data where expected
   - Columns align with expected headers

## 10. Artifacts to store after onboarding
- Capture and store:
  1) Dashboard snapshot
  2) Products, Preorders, Repricing, Wallet, Reports pages (with client data)
  3) Links to any relevant tickets/tasks and the onboarding checklist
  4) Key logs (Kaspi connect/selftest, sync now, import runs)
- Update this runbook with lessons learned after each onboarding.

## Quick commands
```powershell
pwsh -NoProfile -File ./scripts/smoke-auth.ps1 -BaseUrl https://api.example.com -Identifier store_admin@example.com -Password 'replace-me'
pwsh -NoProfile -File ./scripts/smoke-kaspi-sync-now.ps1 -BaseUrl https://api.example.com -MerchantUid <merchant_uid>
pwsh -NoProfile -File ./scripts/smoke-preorders-e2e.ps1 -BaseUrl https://api.example.com
pwsh -NoProfile -File ./scripts/smoke-repricing-e2e.ps1 -BaseUrl https://api.example.com
pwsh -NoProfile -File ./scripts/smoke-reports-wallet-transactions.ps1 -BaseUrl https://api.example.com
```

## Next companies
- For N-th company onboarding, follow docs/runbooks/add_new_company.md.
