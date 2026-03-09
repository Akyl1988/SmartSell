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

## 11. Operator incident evidence cycle (2026-03-09, tenant 1)

This section records one real operator incident-style handling path using existing support/diagnostics APIs only (no DB access, no workflow redesign).

### 11.1 Incident intake (input)
- Trigger: support review of tenant Kaspi integration state.
- Tenant: `company_id=1`.
- Endpoint evidence:
	- `GET /api/v1/admin/tenants/1/diagnostics` -> `HTTP 200`
	- `POST /api/v1/admin/tenants/1/support-triage-preview` -> `HTTP 200`
- Submitted triage payload:
	- `severity=SEV-3`
	- `area=kaspi`
	- `issue_summary="Kaspi support incident simulation: export remains pending while no recent failure is exposed; verify sync freshness and feed pipeline state."`
	- `latest_request_id=ef168bbb-44c1-4395-8b46-337c1b3273ac`

### 11.2 Diagnostics lookup snapshot
- `kaspi.connected=true`
- `kaspi.last_successful_sync_at=2026-02-21T04:24:20.557748`
- `kaspi.last_failed_sync_at=null`
- `kaspi.last_error_summary=null`
- `kaspi.last_export_status=pending`
- `kaspi.last_import_status=null`
- `support.last_request_id=ef168bbb-44c1-4395-8b46-337c1b3273ac`

### 11.3 Triage classification output (existing workflow)
- Triage endpoint returned:
	- `severity=SEV-3`
	- `area=kaspi`
	- `status=preview`
	- `normalized=true`
	- `automation_supported=false`
	- `diagnostics_endpoint=/api/v1/admin/tenants/1/diagnostics`
- Recommended next steps from preview:
	- `confirm_tenant`
	- `classify_area`
	- `fetch_diagnostics`
	- `determine_impact`
	- `choose_next_action`
	- `collect_evidence`
	- `mark_status`

### 11.4 Operator next action recommendation
- Treat as **SEV-3 kaspi integration triage** (degraded/needs follow-up, no active failure signal).
- Execute controlled Kaspi feed/export pipeline verification and sync freshness check.
- Track by `latest_request_id` and attach follow-up outcome to incident timeline.

### 11.5 Incident note (postmortem-style record)
```text
Postmortem ID: PM-INC-SIM-2026-03-09-01
Incident ID: INC-SIM-2026-03-09-01
Date: 2026-03-09
Owner: Founder/Ops

Summary:
Operator executed full support incident-style path for tenant 1 using diagnostics + support-triage-preview endpoints.

Impact:
No active outage detected; potential Kaspi feed/sync freshness concern for one tenant support case.

Timeline (UTC):
- Detection: support review opened from tenant integration visibility check.
- Triage: diagnostics fetched (HTTP 200), severity/area classified as SEV-3/kaspi via triage preview (HTTP 200).
- Mitigation: immediate recommendation prepared (verify feed/export pipeline and sync freshness).
- Resolution: simulation cycle completed with evidence recorded.

Root cause:
Not a confirmed platform failure; this record validates operator process execution path.

Corrective / preventive follow-up:
1) Re-run same operator cycle on additional real support cases.
2) Accumulate complete multi-incident timelines before promoting Incident process to Exists.

Evidence links:
- /api/v1/admin/tenants/1/diagnostics
- /api/v1/admin/tenants/1/support-triage-preview
- SMARTSELL_TENANT_DIAGNOSTICS_SUMMARY.md
```

## 12. Operator incident evidence cycle #2 (2026-03-09, tenant 1, scenario A: kaspi stale/export pending)

### 12.1 Intake
- Tenant/company: `company_id=1` (`Dev Company`)
- Incident input:
	- `severity=SEV-3`
	- `area=kaspi`
	- `issue_summary="Kaspi support review: export status is 'pending' while last sync success is '02/21/2026 04:24:20' and no hard failure is reported."`
	- `latest_request_id=ef168bbb-44c1-4395-8b46-337c1b3273ac`

### 12.2 Diagnostics snapshot
- Endpoint call:
	- `GET /api/v1/admin/tenants/1/diagnostics` -> `CYCLEA_DIAGNOSTICS_HTTP=200`
- Raw diagnostics highlights:
	- `kaspi.connected=true`
	- `kaspi.last_successful_sync_at=2026-02-21T04:24:20.557748`
	- `kaspi.last_failed_sync_at=null`
	- `kaspi.last_error_summary=null`
	- `kaspi.last_export_status=pending`
	- `support.last_request_id=ef168bbb-44c1-4395-8b46-337c1b3273ac`

### 12.3 Triage classification output
- Endpoint call:
	- `POST /api/v1/admin/tenants/1/support-triage-preview` -> `CYCLEA_TRIAGE_HTTP=200`
- Raw triage output:
	- `severity=SEV-3`
	- `area=kaspi`
	- `status=preview`
	- `normalized=true`
	- `diagnostics_endpoint=/api/v1/admin/tenants/1/diagnostics`
	- `recommended_next_steps=[confirm_tenant, classify_area, fetch_diagnostics, determine_impact, choose_next_action, collect_evidence, mark_status]`

### 12.4 Operator next action
- Continue as support degradation review (no hard failure signal).
- Verify export pipeline progression from `pending` and confirm sync freshness window for tenant communication.

### 12.5 Customer update note
- Current status: Investigating (low-impact support review).
- What is affected: Kaspi integration requires operator verification due to pending export state.
- What is not affected: No hard sync failure is currently reported in diagnostics.
- Next update: after export pipeline follow-up and freshness check.

### 12.6 Closure note
- Cycle closed as triage-evidence complete: diagnostics and triage contract both returned `200` and produced actionable next steps.
- No active outage declared for this cycle.

### 12.7 Corrective / preventive follow-up
1. Track recurring `last_export_status=pending` duration in repeated support checks.
2. Keep request-id correlation (`support.last_request_id`) in every follow-up note.

## 13. Operator incident evidence cycle #3 (2026-03-09, tenant 1, scenario B: billing/lifecycle clarification)

### 13.1 Intake
- Tenant/company: `company_id=1` (`Dev Company`)
- Incident input:
	- `severity=SEV-3`
	- `area=billing`
	- `issue_summary="Billing/lifecycle support review: subscription_state 'active', billing.state 'active', grace_until '', and last_payment_status '' require operator clarification for tenant communication."`
	- `latest_request_id=ef168bbb-44c1-4395-8b46-337c1b3273ac`

### 13.2 Diagnostics snapshot
- Endpoint call:
	- `GET /api/v1/admin/tenants/1/diagnostics` -> `CYCLEB_DIAGNOSTICS_HTTP=200`
- Raw diagnostics highlights:
	- `subscription_state=active`
	- `billing.state=active`
	- `billing.grace_until=null`
	- `billing.last_payment_status=null`
	- `lifecycle_state=ACTIVE`
	- `support.last_request_id=ef168bbb-44c1-4395-8b46-337c1b3273ac`

### 13.3 Triage classification output
- Endpoint call:
	- `POST /api/v1/admin/tenants/1/support-triage-preview` -> `CYCLEB_TRIAGE_HTTP=200`
- Raw triage output:
	- `severity=SEV-3`
	- `area=billing`
	- `status=preview`
	- `normalized=true`
	- `diagnostics_endpoint=/api/v1/admin/tenants/1/diagnostics`
	- `recommended_next_steps=[confirm_tenant, classify_area, fetch_diagnostics, determine_impact, choose_next_action, collect_evidence, mark_status]`

### 13.4 Operator next action
- Treat as billing communication-clarification case (not an active payment failure incident).
- Provide tenant-facing explanation of current active state and what signals would indicate grace/suspension transition.

### 13.5 Customer update note
- Current status: Clarified.
- What is affected: no active billing lock indicated at this time.
- What is not affected: tenant remains in active lifecycle/billing state.
- Next update: only if billing state changes (grace/suspension trigger) or customer reports impact.

### 13.6 Closure note
- Cycle closed as informational billing review with complete evidence trail (`diagnostics=200`, `triage=200`) and documented operator decision.

### 13.7 Corrective / preventive follow-up
1. Reuse the same billing triage template for future state-change communications.
2. Escalate severity to Sev-2 only when diagnostics indicate real degradation (grace/suspended or hard failure signal).
