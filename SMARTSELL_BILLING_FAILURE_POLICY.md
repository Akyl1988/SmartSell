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
