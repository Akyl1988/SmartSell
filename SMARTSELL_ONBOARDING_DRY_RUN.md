# SMARTSELL_ONBOARDING_DRY_RUN

## 1. Purpose
Record a simulated onboarding execution against the documented playbook to validate that activation steps are reproducible with existing dev/test tenant setup.

## 2. Test tenant used
- Primary simulated tenant context: existing test tenant flow used by `company_a_admin_headers` (tenant A).
- Isolation check context: tenant B (`company_b_admin_headers`) in the same test flow.
- Diagnostics-focused seeded tenant in test: company `9501` (test-only, created in test transaction).

## 3. Preconditions
- SmartSell test environment available.
- API test client fixtures available.
- Existing automated test tenant fixtures available (no production tenant used).
- Onboarding playbook checklist used as dry-run template.

## 4. Steps executed
Executed as one focused dry-run command:

`pytest tests/app/test_auth.py::TestAuth::test_login_with_password tests/app/api/test_admin_tenant_diagnostics.py::test_admin_tenant_diagnostics_summary tests/app/api/test_preorders_rbac_tenant.py::test_preorders_store_admin_flow_and_tenant_isolation -q`

Observed result:
- `3 passed in 10.63s`

Step mapping to playbook:
1. Admin access verification (`test_login_with_password`).
2. Tenant diagnostics verification (`test_admin_tenant_diagnostics_summary`).
3. First core flow and tenant isolation verification (`test_preorders_store_admin_flow_and_tenant_isolation`).

## 5. Validation checks performed
- [x] Admin access verified.
- [x] Tenant diagnostics endpoint validated.
- [x] First core flow executed successfully (preorder create/confirm/fulfill path in test flow).
- [x] Tenant isolation behavior validated in core flow test.
- [ ] Real integration connectivity check executed in this dry run (not covered directly by this command set).
- [ ] Real production billing-state review performed (simulated test context only).

## 6. Issues encountered
- No blocking issues in simulated run.
- Gap noted: integration connectivity and operator evidence packaging are still partially manual outside this focused automated run.

## 7. Outcome
- Simulated onboarding dry run succeeded in test environment.
- Playbook is actionable for core onboarding checks using reproducible test flows.
- Result does not represent production tenant activation; it is evidence of process rehearsal only.

## 8. Improvements needed for playbook
1. Add a dedicated "integration connectivity evidence" step output template (Kaspi/non-Kaspi).
2. Add a single consolidated onboarding evidence form so operators can attach command/test outputs consistently.
3. Add explicit "billing state snapshot capture" command/API step to reduce manual interpretation.
