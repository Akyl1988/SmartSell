# SMARTSELL_FULL_TENANT_SIMULATION

## 1. Purpose
Record a repository-grounded, end-to-end tenant simulation to assess launch readiness for first real tenants.

## 2. Test tenant / user used
- Test context: existing automated test tenant fixtures (tenant A / tenant B headers and seeded test entities).
- User context: store-admin auth flow from existing test suite (`TestAuth`).
- No production customer/tenant data used.

## 3. Preconditions
- Local SmartSell test environment available.
- Existing DB/test fixtures available.
- Auth, diagnostics, preorder, Kaspi-connect, billing-state, and reports test modules present.

## 4. Steps executed
Single simulation command executed:

`pytest tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/test_auth.py::TestAuth::test_logout_revokes_session tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/services/test_billing_state_machine.py::test_active_to_grace_resolution tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation tests/app/api/test_kaspi_connect.py::TestKaspiConnect::test_connect_verify_false_skips_adapter tests/app/test_reports_preorders_csv.py::test_preorders_csv_ok_for_admin -q`

Observed output:
- `7 passed in 13.82s`

## 5. Results by step
- **Auth/login**: PASS (`test_login_with_password`).
- **Tenant/admin access + isolation in business flow**: PASS (`test_preorders_store_admin_flow_and_tenant_isolation`).
- **Diagnostics visibility**: PASS (`test_admin_tenant_diagnostics_summary`).
- **Billing/subscription state behavior**: PASS (`test_active_to_grace_resolution`).
- **Core business flow (preorder lifecycle path)**: PASS (`test_preorders_store_admin_flow_and_tenant_isolation`).
- **Kaspi-related validation**: PASS (`test_connect_verify_false_skips_adapter`).
- **Report/export validation**: PASS (`test_preorders_csv_ok_for_admin`).
- **Logout/session sanity**: PASS (`test_logout_revokes_session`).

## 6. Issues encountered
- No blocking failures in this simulation run.
- Limitations: this is a test-environment simulation, not a live production tenant rehearsal.

## 7. Final outcome
- Simulated full-tenant flow passed across the minimum required launch-critical areas.
- Evidence supports improved confidence for early-tenant onboarding in operator-assisted mode.
- This is strong readiness evidence, but not full production proof.

## 8. Remaining risks before first 10 clients
- No repeated real-tenant production onboarding evidence yet.
- Some operational checks remain partially manual (integration verification and evidence packaging).
- Frontend auth hardening improved, but additional runtime race/UX coverage should continue.
- DR/release readiness still depends on repeated real execution evidence, not single simulation.

## 9. Full operational tenant simulation evidence.

Operational simulation executed on 2026-03-09 using existing runtime flows only (no app code changes, no new endpoints).

### 9.1 Runtime path executed
1. Store user authentication via existing script:
	- `pwsh -NoProfile -File .\scripts\smoke-auth.ps1 -BaseUrl http://127.0.0.1:8000`
2. Subscription state check:
	- `GET /api/v1/subscriptions/current`
3. Kaspi integration check:
	- `GET /api/v1/kaspi/status`
4. Product catalog check:
	- `GET /api/v1/products?page=1&per_page=1`
5. Inventory check:
	- `GET /api/v1/inventory/stocks`
6. Orders sync:
	- `POST /api/v1/kaspi/orders/sync`
7. Order lifecycle smoke:
	- `pwsh -NoProfile -File .\scripts\smoke-orders-lifecycle.ps1 -BaseUrl http://127.0.0.1:8000`
8. Preorder smoke:
	- `pwsh -NoProfile -File .\scripts\smoke-preorders-e2e.ps1 -BaseUrl http://127.0.0.1:8000`
9. Reports export verification:
	- `GET /api/v1/reports/wallet/transactions.csv`

### 9.2 Evidence markers and observed outputs
- `TENANT_SIMULATION_START subscription_http=200 subscription_status=active`
- `TENANT_SIMULATION_PRODUCTS http=200`
- `TENANT_SIMULATION_ORDERS_SYNC http=200`
- `TENANT_SIMULATION_ORDER_LIFECYCLE exit_code=0`
- `TENANT_SIMULATION_PREORDERS exit_code=0`
- `TENANT_SIMULATION_REPORTS http=200`
- `TENANT_SIMULATION_COMPLETE subscription=200 kaspi=200 products=200 inventory=200 orders_sync=200 order_lifecycle_exit=0 preorders_exit=0 reports=200`

### 9.3 Runtime status summary
- `GET /api/v1/subscriptions/current` -> `200` (`status=active`)
- `GET /api/v1/kaspi/status` -> `200`
- `GET /api/v1/products?page=1&per_page=1` -> `200`
- `GET /api/v1/inventory/stocks` -> `200`
- `POST /api/v1/kaspi/orders/sync` -> `200`
- `scripts/smoke-orders-lifecycle.ps1` -> `exit_code=0`
- `scripts/smoke-preorders-e2e.ps1` -> `exit_code=0`
- `GET /api/v1/reports/wallet/transactions.csv` -> `200`

Conclusion: full operational tenant lifecycle is validated on existing runtime path from auth and billing-gated readiness checks through catalog/inventory, orders sync, order/preorder lifecycles, and reports export.
