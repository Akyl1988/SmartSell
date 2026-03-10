# SMARTSELL_ONBOARDING_PLAYBOOK

## 1. Purpose
Provide a repeatable, operator-safe onboarding flow for first real SmartSell tenants so activation does not depend on founder memory.

## 2. Scope
- First 10 production tenants.
- Covers tenant activation readiness, initial validation, and evidence collection.
- Operator-assisted flow; automation may be partial.

## 3. Onboarding owner
- Exactly one onboarding owner per tenant (default: Founder/Ops).
- Owner is accountable for checklist completion, go/no-go decision, and evidence pack.

## 4. Preconditions before onboarding
- [ ] Tenant/company record exists or is created and verified.
- [ ] Responsible customer contact and activation window confirmed.
- [ ] Required credentials/secrets available for enabled integrations.
- [ ] Rollback owner and rollback path agreed before activation.

## 5. Tenant onboarding steps
1. Confirm/create tenant company profile.
2. Confirm at least one admin user exists.
3. Verify admin login and basic access.
4. Check subscription/billing state is valid for launch (`trial`/`active`/approved override).
5. Configure and validate integration connectivity if applicable (e.g., Kaspi).
6. Verify tenant diagnostics endpoint reflects expected baseline state.
7. Run one first core flow end-to-end (for example: product/inventory/order or preorder flow).
8. Record outcome and proceed to activation decision.

## 6. Required validation checks
- [ ] Company/tenant isolation sanity check passes.
- [ ] Admin access verified (login + authorized route).
- [ ] Subscription/billing state reviewed and not blocking activation.
- [ ] Integration connectivity checked (if tenant uses integration).
- [ ] Tenant diagnostics checked for obvious failure signals.
- [ ] First core business flow verified successfully.

## 7. Rollback / abort conditions
Abort onboarding activation if any of the following is true:
- Admin access cannot be verified.
- Billing/subscription state is unresolved and would block use.
- Required integration is failing with no workable mitigation.
- Core flow fails and cannot be fixed in activation window.

Rollback/abort path:
- Keep tenant in non-active launch state.
- Revert/disable newly enabled integration settings if needed.
- Record incident or onboarding blocker with owner and next action date.

## 8. Activation criteria
Tenant is considered activated only when all are true:
- Checklist owner signs off.
- Required validation checks passed.
- No unresolved Sev1/Sev2 onboarding blocker.
- Evidence pack collected and stored.

## 9. Evidence pack required after onboarding
- Tenant/company identifier and activation timestamp.
- Admin access verification proof.
- Subscription/billing state snapshot.
- Diagnostics snapshot (`/api/v1/admin/tenants/{company_id}/diagnostics`).
- Integration check output (if applicable).
- First core flow verification note/output.
- Rollback decision note (used/not used).

## 10. What is still manual vs what is already standardized
Standardized now:
- Required checklist structure.
- Single owner rule.
- Mandatory validation and evidence pack.

Still manual now:
- Some integration verification steps.
- Final go/no-go operator judgment.
- Evidence aggregation into one onboarding packet.

## 11. Evidence required to move from Partial to Exists
- At least 3 real tenant onboardings completed using this exact playbook.
- Each onboarding has complete evidence pack with owner sign-off.
- No activation performed outside this process during observation window.
- Common manual pain points identified and either automated or formally documented.
