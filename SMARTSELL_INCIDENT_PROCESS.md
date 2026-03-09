# SMARTSELL_INCIDENT_PROCESS

## 1. Purpose
Provide a lightweight, founder-operable incident process for the first 10 clients so SmartSell can respond fast, communicate clearly, and restore service safely.

Scope: production-impacting issues in multi-tenant operations, including integrations, billing, auth/session, order sync, and deployment/migration failures.

## 2. Incident severity levels

### Sev 1 (Critical)
- Major outage or data-risk event affecting many tenants or core platform access.
- Target acknowledgement: within 15 minutes.
- Typical SmartSell examples:
	- Authentication/session outage (users cannot log in)
	- Migration/deploy issue causing API downtime
	- Orders not syncing for multiple active tenants

### Sev 2 (High)
- Major feature degraded for one or several tenants; workaround may exist.
- Target acknowledgement: within 30 minutes.
- Typical SmartSell examples:
	- Kaspi sync failure for key tenants
	- Billing renewal failure causing unexpected access problems
	- Orders delayed or partially syncing for a subset of tenants

### Sev 3 (Medium/Low)
- Limited impact, non-critical degradation, cosmetic or isolated support issue.
- Target acknowledgement: within 4 business hours.
- Typical SmartSell examples:
	- Intermittent Kaspi sync retries with no hard failure
	- Single-tenant billing status mismatch with manual workaround
	- Non-blocking admin/reporting error

## 3. Incident owner rule
- Exactly one Incident Owner is assigned at incident open.
- For first 10 clients, default owner is Founder/Ops unless delegated.
- Owner responsibilities:
	- Classify severity
	- Coordinate technical response
	- Publish internal/customer updates
	- Decide escalation and closure
- If owner changes, record handoff time and new owner in updates.

## 4. Initial response checklist
- [ ] Open incident record (time, trigger, affected tenants, suspected area).
- [ ] Assign Incident Owner.
- [ ] Set initial severity (Sev 1/2/3).
- [ ] Confirm blast radius (single tenant vs multi-tenant).
- [ ] Freeze risky non-essential deploys until stabilized.
- [ ] Capture first evidence: logs, request IDs, failing endpoints/jobs.
- [ ] Post first internal update (within SLA window).
- [ ] If customer impact exists, send first customer-facing notice.

## 5. Customer communication rule
- For Sev 1/Sev 2 with customer impact, send first customer message immediately after triage.
- Use plain language: what is affected, what is not affected, next update time.
- Do not speculate on root cause before confirmation.
- Update cadence:
	- Sev 1: every 30–60 minutes
	- Sev 2: every 60–120 minutes
	- Sev 3: at major milestones or resolution
- Always include current workaround (if any).

## 6. Internal incident update template
Use in chat/journal:

```
[INCIDENT UPDATE]
Time (UTC):
Incident ID:
Severity:
Owner:
Status: Investigating | Identified | Mitigating | Monitoring | Resolved
Affected scope: (tenants/services)
What changed since last update:
Current hypothesis:
Actions in progress:
Risks / blockers:
Next update at:
```

## 7. Customer-facing update template
Use for tenant communication:

```
Subject: SmartSell service update

Current status: [Investigating/Identified/Mitigating/Resolved]
What is affected: [brief]
What is not affected: [brief]
Start time (UTC):
Current impact scope: [single tenant / multiple tenants]
Workaround (if available):
Next update by (UTC):

We are actively working on this and will provide the next update by the time above.
```

## 8. Resolution checklist
- [ ] Service behavior restored and validated.
- [ ] Tenant impact verified (sample affected tenants checked).
- [ ] Monitoring/logs stable for agreed window (minimum 30 minutes for Sev 1/2).
- [ ] Temporary mitigations reviewed (keep/remove documented).
- [ ] Customer resolution update sent.
- [ ] Incident marked resolved with final timeline.

## 9. Postmortem template
Complete for Sev 1 and Sev 2 (recommended for Sev 3 with recurring pattern).

```
Postmortem ID:
Incident ID:
Date:
Owner:

Summary:
Impact (tenants, duration, business effect):
Timeline (UTC):
- Detection:
- Triage:
- Mitigation:
- Resolution:

Root cause:
Contributing factors:
What worked well:
What failed:

Corrective actions:
1)
2)

Preventive actions:
1)
2)

Evidence links (logs/request IDs/PRs):
```

## 10. Exit criteria for considering Incident Process as Partial/Exists

### Partial
- This document exists and is used for active incidents.
- Severity rubric, owner rule, and update templates are followed.
- At least one real incident has been tracked with this process.

### Exists
- Process is used consistently for all Sev 1/Sev 2 incidents.
- At least two incidents include complete timeline + customer updates + closure notes.
- At least one postmortem completed with corrective actions tracked.
