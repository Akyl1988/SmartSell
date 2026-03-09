| Area | Priority | Status | Owner | ETA | Evidence Reference | Exit Criteria | Blockers |
|---|---|---|---|---|---|---|---|
| Runtime ownership split | P0 | Partial | Founder/Backend | 3-5 days |  | API request-only; background work only in worker/scheduler roles | Lifecycle coupling |
| Frontend auth/session hardening | P0 | Partial | Founder/Frontend | 2-4 days |  | Hardened session/token strategy; revoke/logout tested | Current frontend storage model |
| Standard onboarding playbook | P0 | Partial | Founder/Ops | 2-3 days |  | One checklist, one owner, one rollback path, one evidence pack | No standardized activation flow |
| Tenant diagnostics summary | P0 | Partial | Founder/Backend | 3-5 days | SMARTSELL_TENANT_DIAGNOSTICS_SUMMARY.md<br>GET /api/v1/admin/tenants/{company_id}/diagnostics<br>tests/app/api/test_admin_tenant_diagnostics.py | Tenant support surface shows sync/error/request/integration health | Data scattered |
| Billing state machine | P0 | Partial | Founder/Product+Backend | 2-3 days |  | Subscription states and transitions defined | Policy decisions pending |
| Billing failure/grace/suspension policy | P0 | Partial | Founder/Product+Backend | 2-3 days |  | Subscription state machine written and supportable | Policy decisions pending |
| DR baseline and restore drill | P0 | Partial | Founder/Ops | 2-4 days | SMARTSELL_DR_RESTORE_DRILL.md | Restore drill completed; RPO/RTO documented | No completed drill evidence |
| Incident process | P0 | Partial | Founder/Ops | 1-2 days | SMARTSELL_INCIDENT_PROCESS.md | Severity rubric, owner rule, templates exist | No process |
| Kaspi support visibility | P0 | Partial | Founder/Backend | 3-5 days |  | Last success/failure visible; errors understandable | Integration complexity |
| Release checklist and smoke gate | P0 | Partial | Founder/Ops | 1-2 days | SMARTSELL_RELEASE_CHECKLIST.md<br>SMARTSELL_RELEASE_DRY_RUN_EVIDENCE.md | Release checklist documented and used | No single enforced gate |