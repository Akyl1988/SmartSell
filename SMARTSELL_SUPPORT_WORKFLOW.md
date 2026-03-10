# SmartSell Support Workflow / Triage Lane (MVP)

Version: 2026-03-09
Status: Policy + preview contract only

## 1) Single intake path

Tenant issues are handled through one operator intake path.

Required identifiers:
- company_id
- issue_summary
- severity
- first_observed_at
- latest_request_id (if available)

## 2) Severity rubric

### SEV-1
- Definition: Production outage or critical business stop for tenant.
- Response expectation: Immediate triage and active owner assignment.
- Escalation rule: Escalate to platform leadership immediately.

### SEV-2
- Definition: Major degradation with material business impact.
- Response expectation: Same-day triage with mitigation path.
- Escalation rule: Escalate to domain owner if unresolved.

### SEV-3
- Definition: Moderate issue with workaround available.
- Response expectation: Planned triage in normal support lane.
- Escalation rule: Escalate if repeated or impact expands.

### SEV-4
- Definition: Low impact request/question or cosmetic issue.
- Response expectation: Backlog triage and standard response.
- Escalation rule: No immediate escalation required.

## 3) Triage flow

1. Confirm tenant
2. Classify area
3. Fetch diagnostics
4. Determine impact
5. Choose next action
6. Collect evidence
7. Mark status

## 4) Allowed area categories

- auth
- billing
- kaspi
- repricing
- preorder
- reports
- integrations
- platform

## 5) Not implemented yet

- no ticket queue
- no SLA automation
- no pager integration
- no notification workflow
