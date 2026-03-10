# SmartSell Data Retention Policy

Version: 2026-03-09
Status: Policy-only (no destructive cleanup jobs enabled)
Owner: Backend + Ops

## Scope

This policy defines how long operational data is retained in SmartSell.
It does not introduce automatic deletion in this phase.

## Retention Rules

| Data Type | Retention Period | Storage Tier | Cleanup Strategy |
|---|---:|---|---|
| Orders | 3650 days (10 years) | Primary DB (hot) | Future scheduled archival + legal-hold-aware purge |
| Campaigns | 730 days (2 years) | Primary DB (warm) | Future soft-delete compaction and archive export |
| Logs (application/audit) | 180 days | Log storage (warm) | Future rolling retention window |
| Integration Events | 365 days | Primary DB (warm) | Future age-based archive/purge job |
| Reports (generated artifacts/exports) | 180 days | Object/File storage (warm/cold) | Future TTL cleanup + optional on-demand keep |
| Diagnostics Snapshots | 90 days | Primary DB / snapshot store (warm) | Future periodic snapshot pruning |

## Notes

- This document defines policy and target limits only.
- Actual cleanup workers/jobs will be implemented in a later phase.
- Any legal/compliance hold must override default retention windows.
