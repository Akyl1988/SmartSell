# SmartSell Tenant Archive/Delete Policy (MVP)

Version: 2026-03-09
Status: Policy-only preview contract (no destructive deletion)

## Allowed lifecycle states

- active
- archived
- delete_requested
- pending_export
- pending_purge

## Rules

- Delete cannot proceed without export-before-delete.
- Archived tenant is not treated as active customer.
- `delete_requested` does not mean immediate physical deletion.
- Purge is delayed and can run only in a separate explicitly confirmed process.

## Operator policy

- Platform admin only.
- Mandatory reason.
- Mandatory evidence trail.
- Mandatory export manifest reference before delete request is approved.

## Not implemented in this MVP

- No destructive delete.
- No background purge worker.
- No object storage cleanup.
- No legal-hold automation.
