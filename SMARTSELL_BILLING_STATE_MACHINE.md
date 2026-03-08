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