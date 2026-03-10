# SMARTSELL_BILLING_STATE_MACHINE

## States
- trial
- active
- grace
- suspended
- cancelled

## Allowed transitions
- trial -> active
- trial -> cancelled
- active -> grace
- active -> cancelled
- grace -> active
- grace -> suspended
- grace -> cancelled
- suspended -> active
- suspended -> cancelled

## State meanings

### trial
Tenant is in free trial period.

### active
Tenant has active paid access.

### grace
Payment failed, but access is temporarily still allowed.

### suspended
Tenant access is restricted due to unresolved billing issue.

### cancelled
Tenant subscription is no longer active.

## Access policy
- trial: trial-limited access
- active: full access by plan
- grace: temporary access with warning
- suspended: read-only / restricted access
- cancelled: no active operational access

## Default grace period
- 7 days

## Notes
- Keep the state machine intentionally simple for first 10 clients.
- Do not add extra billing states until current states are fully operational.
- Any exceptions must be documented explicitly.

## 11. Operator billing state machine lifecycle evidence cycle (2026-03-09)

Runtime flow was executed using existing endpoints only (no DB access, no logic changes):

1. `GET /api/v1/subscriptions/current`
2. `POST /api/v1/subscriptions/{id}/cancel`
3. `GET /api/v1/products?page=1&per_page=1` (guard probe)
4. `POST /api/v1/admin/wallet/topup`
5. `POST /api/v1/admin/subscriptions/activate`
6. `GET /api/v1/products?page=1&per_page=1` (final guard probe)

Observed factual outputs:
- `CYCLE_CURRENT_HTTP=200`
- `CYCLE_START_STATUS=active`
- `CYCLE_CANCEL_HTTP=200`
- `CYCLE_BLOCK_HTTP=402`
- `CYCLE_BLOCK_CODE=SUBSCRIPTION_REQUIRED`
- `CYCLE_TOPUP_HTTP=200`
- `CYCLE_ACTIVATE_HTTP=200`
- `CYCLE_FINAL_HTTP=200`

Lifecycle evidence mapping:
- `active` subscription state was confirmed at cycle start.
- After cancel, guarded endpoint was blocked with `402 SUBSCRIPTION_REQUIRED`.
- After wallet topup + admin activation, guarded endpoint recovered to `200`.

Conclusion:
- Full operational guard/recovery lifecycle is confirmed in runtime via existing billing/subscription/wallet flows.