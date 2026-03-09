# SMARTSELL_BILLING_FAILURE_POLICY

## 1. Purpose
Define a clear, operator-facing policy for payment failure handling so SmartSell can manage billing risk without abrupt customer lockout during first-client launch.

## 2. Scope
- Applies to paid tenant subscriptions in SmartSell.
- Covers transitions and operator actions around payment failure, grace, suspension, and reactivation.
- For first 10 paying tenants, policy is intentionally manual-safe and operator-assisted.

## 3. Subscription states involved
Aligned with `SMARTSELL_BILLING_STATE_MACHINE.md`:
- `active`
- `grace`
- `suspended`
- `cancelled`

Notes:
- `trial` handling remains separate from failed paid renewals.
- State transitions must stay within the approved billing state machine.

## 4. Failed payment policy
- A failed payment **does not instantly hard-lock** the tenant.
- On first verified renewal/payment failure for an active paid tenant:
	- Set/confirm billing state as `grace`.
	- Record failure reason/code if available.
	- Notify customer that billing failed and grace period started.
- For first-client maturity, operator may perform manual verification before enforcing further restriction.

## 5. Grace period policy
- Default grace period target: **7 days**.
- During grace:
	- Tenant keeps temporary operational access.
	- Billing reminder communication is sent (at least initial + final warning).
	- Support can assist with payment method correction and confirmation.
- If payment is resolved during grace, transition back to `active`.

## 6. Suspension policy
- If grace expires with unresolved billing, tenant transitions to `suspended`.
- Suspension behavior (minimum):
	- No full operational access for business-critical write operations.
	- Access is restricted to billing/support remediation path where possible.
- Suspension must be explicit in operator view and support response.

## 7. Reactivation policy
- Reactivation path must be explicit and reversible:
	1. Verify successful payment or approved admin override.
	2. Confirm tenant entitlement/plan state.
	3. Transition `suspended`/`grace` tenant back to `active`.
	4. Notify customer that access is restored.
- Reactivation should be logged with timestamp and actor (operator/system).

## 8. Manual admin override policy
- Platform admin override is explicitly allowed for first-client operations.
- Allowed uses:
	- Temporary extension during payment processor issues.
	- Commercial exception approved by founder/business owner.
	- Incident mitigation where strict enforcement would cause disproportionate harm.
- Override requirements:
	- Reason documented.
	- Time-bounded expiry set.
	- Follow-up owner assigned.

## 9. Customer-facing communication expectations
- Billing failure communication should be clear and non-technical:
	- what failed,
	- current state (`grace` or `suspended`),
	- deadline/timebox,
	- exact action needed to restore normal access.
- Minimum communication moments:
	- At failure/grace start.
	- Before grace expiry.
	- At suspension (if reached).
	- At reactivation.

## 10. Evidence required to move from Partial to Exists
- Policy is used in at least two real billing failure cases.
- For each case, evidence includes:
	- state transition timeline,
	- customer communications,
	- suspension/reactivation decision log,
	- override record (if used).
- Support/admin surface clearly shows current billing state and grace/suspension context.

## 11. Operational evidence cycle (2026-03-09)

This section records one repository-grounded operator cycle using existing endpoints/scripts only (no DB access).

### 11.1 Targeted transition validation (narrow tests)
- Command:
	- `pytest tests/services/test_billing_state_machine.py::test_active_to_grace_resolution tests/services/test_billing_state_machine.py::test_grace_to_suspended_resolution -q`
- Output:
	- `2 passed in 7.18s`
	- `BILLING_STATE_MACHINE_TARGET_EXIT=0`
- Meaning:
	- `active -> grace` resolution and `grace -> suspended` resolution are validated in current state machine implementation.

### 11.2 Grace access-policy validation (narrow tests)
- Command:
	- `pytest tests/test_billing_wallet_topup.py::test_past_due_within_grace_allows_access tests/test_billing_wallet_topup.py::test_after_grace_access_denied -q`
- Output:
	- `2 passed in 7.79s`
	- `BILLING_GRACE_ACCESS_TESTS_EXIT=0`
- Meaning:
	- Access is allowed during grace window and denied after grace expiry (`blocked` behavior).

### 11.3 Real operator recovery cycle (tenant 1)
- Initial runtime probe (guarded endpoint):
	- `GET /api/v1/products?page=1&per_page=1` -> `402`
	- `code=SUBSCRIPTION_REQUIRED`
- Starting subscription snapshot:
	- `START_SUB_STATUS=active`
	- `START_SUB_PLAN=Pro`
	- `START_SUB_ID=7`
- Transition trigger:
	- `POST /api/v1/subscriptions/7/cancel` -> `POST_TRIGGER_STATUS=canceled`
	- blocked probe remains `402 SUBSCRIPTION_REQUIRED`
- Recovery actions (existing admin flow):
	1. `POST /api/v1/admin/wallet/topup` (`companyId=1`) -> `TOPUP_HTTP=200`, `TOPUP_BALANCE=2000.00`
	2. `POST /api/v1/admin/subscriptions/activate` first attempt -> `409 DUPLICATE_VALUE` (active duplicate exists)
	3. `POST /api/v1/subscriptions/7/cancel` (idempotent recovery prep) -> `CANCEL_STATUS=canceled`
	4. `POST /api/v1/admin/subscriptions/activate` retry -> `ACTIVATE2_HTTP=200`, `ACTIVATE2_STATUS=active`, `ACTIVATE2_SUB_ID=9`
- Resulting reactivated access check:
	- `GET /api/v1/products?page=1&per_page=1` -> `POST_ACTIVATE_PROBE_HTTP=200`

### 11.4 Evidence conclusion
- One complete operator path is now evidenced:
	- blocked billing state signal (`402 SUBSCRIPTION_REQUIRED`) -> wallet/subscription remediation -> active state with restored guarded access (`200`).
- This is strong Partial evidence, but not enough for `Exists` per Section 10 (still missing repeated real failure cases + communication timeline packs).

## 12. Operational evidence cycle #2 (2026-03-09)

Second independent operator cycle was executed using the same existing flows (`subscriptions/current`, `subscriptions/{id}/cancel`, `admin/wallet/topup`, `admin/subscriptions/activate`).

### 12.1 Raw operational outputs
- Starting state:
	- `CYCLE2_CURRENT_HTTP=200`
	- `CYCLE2_START_STATUS=active`
	- `CYCLE2_START_PLAN=Pro`
	- `CYCLE2_START_SUB_ID=11`
	- `CYCLE2_START_PROBE_HTTP=200`
- Transition trigger and blocked signal:
	- `CYCLE2_TRIGGER_ACTION=cancel_subscription`
	- `CYCLE2_CANCEL_HTTP=200`
	- `CYCLE2_POST_TRIGGER_STATUS=canceled`
	- `CYCLE2_BLOCKED_PROBE_HTTP=402`
	- `CYCLE2_BLOCKED_PROBE_CODE=SUBSCRIPTION_REQUIRED`
- Remediation actions and reactivation:
	- `CYCLE2_TOPUP_HTTP=200`
	- `CYCLE2_TOPUP_TX_ID=4`
	- `CYCLE2_TOPUP_BALANCE=6700.00`
	- `CYCLE2_ACTIVATE_HTTP=200`
	- `CYCLE2_RECOVERY_ACTION=admin_activate_subscription`
	- `CYCLE2_RESULT_STATUS=active`
	- `CYCLE2_RESULT_SUB_ID=12`
	- `CYCLE2_RESULT_PERIOD_END=04/09/2026 16:26:42`
- Final guarded access:
	- `CYCLE2_FINAL_PROBE_HTTP=200`

### 12.2 Comparison with cycle #1 and status decision
- Cycle #1 and Cycle #2 both show the same factual operator path:
	- subscription trigger to blocked state (`402 SUBSCRIPTION_REQUIRED`) -> wallet/subscription remediation -> reactivated guarded access (`200`).
- This strengthens confidence that recovery flow is repeatable in operations.
- Status remains **Partial** (honest):
	- Section 10 `Exists` still requires complete incident communication timeline packs and decision logs across real billing incidents.

## 13. Operator billing incident pack (from real cycle #2)

### 13.1 Incident summary
- Incident ID: `BILLING-INC-2026-03-09-01`
- Category: billing / subscription guard recovery
- Summary: tenant access to guarded endpoint became blocked after subscription transition, then was restored using existing wallet topup + admin subscription activation flow.

### 13.2 Affected tenant
- `company_id=1` (`Dev Company`)

### 13.3 Detection signal
- Guarded endpoint probe:
	- `CYCLE2_BLOCKED_PROBE_HTTP=402`
	- `CYCLE2_BLOCKED_PROBE_CODE=SUBSCRIPTION_REQUIRED`

### 13.4 Impact
- Business-critical guarded API access for tenant was blocked until billing remediation completed.
- Scope: single tenant (`company_id=1`).

### 13.5 Timeline (operational outputs)
- Start snapshot:
	- `CYCLE2_CURRENT_HTTP=200`
	- `CYCLE2_START_STATUS=active`
	- `CYCLE2_START_PLAN=Pro`
	- `CYCLE2_START_SUB_ID=11`
	- `CYCLE2_START_PROBE_HTTP=200`
- Transition trigger:
	- `CYCLE2_TRIGGER_ACTION=cancel_subscription`
	- `CYCLE2_CANCEL_HTTP=200`
	- `CYCLE2_POST_TRIGGER_STATUS=canceled`
- Blocked state confirmation:
	- `CYCLE2_BLOCKED_PROBE_HTTP=402`
	- `CYCLE2_BLOCKED_PROBE_CODE=SUBSCRIPTION_REQUIRED`
- Remediation:
	- `CYCLE2_TOPUP_HTTP=200`
	- `CYCLE2_TOPUP_TX_ID=4`
	- `CYCLE2_TOPUP_BALANCE=6700.00`
	- `CYCLE2_ACTIVATE_HTTP=200`
	- `CYCLE2_RECOVERY_ACTION=admin_activate_subscription`
	- `CYCLE2_RESULT_STATUS=active`
	- `CYCLE2_RESULT_SUB_ID=12`
	- `CYCLE2_RESULT_PERIOD_END=04/09/2026 16:26:42`
- Final verification:
	- `CYCLE2_FINAL_PROBE_HTTP=200`

### 13.6 Customer update note (factual template instance)
- Status: Resolved.
- What was affected: temporary access block to guarded API operations due to billing/subscription guard state.
- What was not affected: tenant identity/authentication endpoints remained reachable.
- Action taken: wallet credited and subscription activated through existing admin remediation flow.
- Current result: guarded access restored (`200`).

### 13.7 Closure note
- Closure condition met for this incident record: blocked signal reproduced and resolved with verified final guarded access recovery.
- Closure evidence anchor: `CYCLE2_FINAL_PROBE_HTTP=200`.

### 13.8 Corrective / preventive follow-up
1. Continue capturing the same pack format for future real billing incidents to build repeated evidence baseline.
2. Attach customer communication timestamps per incident to satisfy `Exists` evidence requirement in Section 10.

## Operator billing incident pack #2

### Incident summary
- Incident ID: `BILLING-INC-2026-03-09-02`
- Category: billing guard / suspension recovery
- Summary: previously active tenant access moved to blocked billing guard state after subscription cancel, then was fully restored via operator remediation using existing admin billing flow.

### Affected tenant
- `company_id=1` (`Dev Company`)

### Detection signal
- `CYCLE_BLOCK_HTTP=402`
- `CYCLE_BLOCK_CODE=SUBSCRIPTION_REQUIRED`

### Impact
- Guarded product API access was unavailable for the tenant while billing guard was active.
- Tenant scope: single affected tenant (`company_id=1`).

### Timeline
- subscription cancel:
	- `CYCLE_CANCEL_HTTP=200`
- guard block 402:
	- `CYCLE_BLOCK_HTTP=402`
	- `CYCLE_BLOCK_CODE=SUBSCRIPTION_REQUIRED`
- operator remediation:
	- existing admin recovery path selected (wallet + subscription activation)
- wallet topup:
	- `CYCLE_TOPUP_HTTP=200`
- subscription activation:
	- `CYCLE_ACTIVATE_HTTP=200`
- guard recovery 200:
	- `CYCLE_FINAL_HTTP=200`

### Customer update note
- We observed a temporary billing-related access block to guarded operations for your tenant.
- Our operator completed billing remediation (wallet topup + subscription activation) through the standard admin path.
- Access has been restored and verified by guarded endpoint recovery (`200`).

### Closure note
- Incident is closed after verified recovery of guarded endpoint access.
- Evidence anchor set: `CYCLE_START_STATUS=active` -> `CYCLE_BLOCK_HTTP=402` -> `CYCLE_FINAL_HTTP=200`.

### Corrective / preventive actions
1. Keep recording operator-grade incident packs for each billing block/recovery event.
2. Attach explicit customer communication timestamps in each pack to maintain `Exists`-level evidence quality.
3. Keep lifecycle evidence in sync across this policy and `SMARTSELL_BILLING_STATE_MACHINE.md`.
