# SmartSell — Next Phase Plan (Post CORE_PROD_PLAN)
**Version:** 2026-01-29  
**Owner:** Ақыл  
**Purpose:** Step-by-step roadmap for the next implementation phase after CORE_PROD_PLAN is complete, focused on first Kaspi client value and production-like operability.

---

## 0) Context & Principles

### Current baseline (assumed true)
- Core production hardening is complete and gated by `scripts/prod-gate.ps1`.
- Error contract unified: JSON `{detail, code, request_id}` + response header `X-Request-ID` everywhere.
- Local-only checks are the source of truth until CI resumes (you already use `prod-gate.ps1`).

### Non-negotiables (to keep speed and avoid regressions)
1. **No rewrites.** Only incremental changes with tight tests.
2. **Every epic ends with:** migrations (if needed) + tests + updated docs + journal entry.
3. **Single quality gate:** `pwsh -NoProfile -File .\scripts\prod-gate.ps1` must pass.
4. **No Redis dependency for dev/MVP flows** (as decided).
5. **Root-cause fixes only.** If a symptom appears in ops, fix the cause, not the surface.

### Target outcome
A Kaspi merchant can:
1) Connect store (token + merchantUid)  
2) Import/update catalog (file-based)  
3) Generate & publish feed (upload lifecycle)  
4) Sync orders reliably (with visible status & logs)  
5) Be billable (subscription enforcement on key features)  
6) Operate system (backup/restore + update flow)

---

## 1) Work Method (Step-by-step)

### The loop we follow for every step
1. **Create branch** from `dev`: `feat/<area>-<short>-v1`  
2. Implement smallest slice that changes behavior safely  
3. Add/extend tests (unit + API)  
4. Run:
   - `python -m ruff format .`
   - `python -m ruff check .`
   - `python -m pytest -q`
   - `pwsh -NoProfile -File .\scripts\prod-gate.ps1`
5. Commit, push, PR to `dev`, merge  
6. Fast-forward `main` from `origin/dev` (your standard flow)  
7. Delete feature branch (local + origin)

---

## 2) Milestones (recommended order)

### Milestone A — Integration Observability (Kaspi Onboarding + Logs)
Goal: Every failure has a structured trace (request_id) and a persistent “why”.

### Milestone B — Catalog Import v2 (client-ready)
Goal: Client can import from CSV/XLSX with predictable normalization and reporting.

### Milestone C — Feed Lifecycle (upload → status → publish)
Goal: Official/real “feed upload job” with statuses and troubleshooting.

### Milestone D — Orders Operations
Goal: Reliable sync and “operator view”: last success, last error, and list of recent orders.

### Milestone E — Subscription Enforcement (real)
Goal: Enforce paid features on the endpoints that matter (feed upload, autosync, pricing).

### Milestone F — Ops Minimum (backup/restore, update flow)
Goal: Real deploy/upgrade/restore steps that a future operator can follow.

> Notes:
> - Pricing min/max (dumping) and preorder are upsell add-ons. Do them after feed+catalog are solid.

---

# 3) Detailed Plan (Tasks + DoD)

## Milestone A — Kaspi Onboarding + Integration Logs

### A1. Store connection status (connect/selftest)
**Deliverables**
- Endpoint returns explicit structured status and saves diagnostics.
- Selftest uses short timeouts and never blocks long (≤ 5s).

**Tasks**
- Add/verify fields in Kaspi store token model:
  - `last_selftest_at`, `last_selftest_status`, `last_selftest_error_code`, `last_selftest_error_message`
- Ensure `/api/v1/kaspi/*` endpoints pass `request_id` into service layer and include `X-Request-ID` in response.
- Add fast probe behavior (timeout and no retries) for selftest/health.

**DoD**
- Tests:
  - connect with invalid token → 401/400 (never 500)
  - selftest returns `upstream_unavailable` quickly when Kaspi unreachable
- `prod-gate.ps1` passes.

---

### A2. Persistent integration events log (operator minimum)
**Deliverables**
- A DB table + service to write integration events.
- Read endpoint for recent events.

**Tasks**
- Create model + migration: `integration_events` (or `kaspi_integration_events`)
  - `id`, `company_id`, `merchant_uid`, `kind`, `status`, `error_code`, `error_message`, `request_id`, `occurred_at`, `meta_json`
- Write events from:
  - Kaspi connect
  - feed upload lifecycle
  - orders sync
- Endpoint:
  - `GET /api/v1/integrations/events?kind=kaspi&limit=100` (or Kaspi-scoped)

**DoD**
- Tests: failure path creates event row.
- Doc: where to look (DEPLOYMENT or dedicated doc).
- `prod-gate.ps1` passes.

---

## Milestone B — Catalog Import v2 (CSV/XLSX + normalization + reporting)

### B1. Input formats and canonical mapping
**Deliverables**
- Import supports CSV now + XLSX (recommended) + optional JSON.
- Canonical columns and alias map are stable.

**Tasks**
- Implement parser abstraction:
  - `parse_catalog_file(file_bytes, filename) -> rows`
- CSV: robust delimiter detection; proper UTF-8 handling.
- XLSX: `openpyxl` parsing (first sheet) with header row detection.
- Normalization:
  - `sku`, `masterSku`, `title`, `price`, `oldprice`, `stockCount`, `preOrder`, `images`, `attributes`
- Validation:
  - per-row error reasons (missing sku, invalid price, etc.)

**DoD**
- Tests on multiple header variants.
- Import response includes:
  - `rows_total`, `rows_ok`, `rows_skipped`, `top_errors`
- `prod-gate.ps1` passes.

---

### B2. Upsert + dry-run + safety rules
**Deliverables**
- Idempotent upsert by `(company_id, merchant_uid, sku)`.
- Dry-run mode returns report without DB writes.

**Tasks**
- Add query param `dry_run=true` to import endpoint.
- Upsert rules:
  - do not overwrite canonical fields with empty values
  - prevent known bad alias collisions (regression tests)
- Optional: store import batch record with report and file fingerprint.

**DoD**
- Tests:
  - repeated import does not create duplicates
  - dry-run does not write
- `prod-gate.ps1` passes.

---

### B3. Client template export
**Deliverables**
- Template CSV/XLSX for client to fill.

**Tasks**
- `GET /api/v1/kaspi/catalog/template?format=csv|xlsx`
- Include minimal example row and required columns.

**DoD**
- Template can be imported with 0 errors (if filled correctly).
- Document usage.

---

## Milestone C — Feed Upload Lifecycle (upload → status → publish)

### C1. FeedUploadJob model + state machine
**Deliverables**
- Job table with statuses and importCode tracking.

**Tasks**
- Migration + model: `kaspi_feed_uploads`
  - `id`, `company_id`, `merchant_uid`, `source`, `status`,
    `import_code`, `attempts`, `last_error_code`, `last_error_message`,
    `created_at`, `updated_at`, `request_id`
- Service:
  - create job
  - upload feed (Kaspi adapter)
  - poll status
  - publish (if applicable)
- Ensure idempotency: repeated request with same `request_id` returns same job.

**DoD**
- Tests for state transitions.
- No double uploads from retries.
- `prod-gate.ps1` passes.

---

### C2. API endpoints for lifecycle
**Deliverables**
- `POST /api/v1/kaspi/feed/uploads`
- `GET /api/v1/kaspi/feed/uploads`
- `GET /api/v1/kaspi/feed/uploads/{id}`
- `POST /api/v1/kaspi/feed/uploads/{id}/refresh`
- `POST /api/v1/kaspi/feed/uploads/{id}/publish` (if needed)

**DoD**
- Contract tests on codes + request_id.
- Clear error codes (`kaspi_upstream_unavailable`, `invalid_token`, etc.)
- Doc “Golden path”.

---

### C3. Documentation: “Kaspi feed from zero to publish”
**Deliverables**
- A clear guide for a non-technical merchant admin.

**Tasks**
- Add to `docs/DEPLOYMENT.md` or `docs/KASPI_FEED.md`:
  - how to generate feed
  - how to upload
  - how to find status
  - what errors mean

**DoD**
- Doc includes exact endpoints and sample commands.

---

## Milestone D — Orders Operations

### D1. Sync reliability + operator status
**Deliverables**
- Visible last sync status per store/company.

**Tasks**
- Persist:
  - `last_orders_sync_at`, `last_orders_sync_status`, `last_orders_sync_error`
- Ensure scheduled sync uses PROCESS_ROLE gating and advisory lock.

**DoD**
- Tests: sync sets last status; errors recorded.
- `prod-gate.ps1` passes.

---

### D2. Minimal “orders list” endpoint (for client value)
**Deliverables**
- Client can view recent orders without DB access.

**Tasks**
- `GET /api/v1/orders?limit=50` (or `GET /api/v1/kaspi/orders?limit=50`)
- Return: id, created_at, status, total, customer summary (as allowed), items count.

**DoD**
- Tenant isolation tests.
- Basic pagination.

---

## Milestone E — Subscription Enforcement (real enforcement)

### E1. Feature matrix (without pricing)
**Deliverables**
- Plans and features defined (prices remain external).

**Tasks**
- Document matrix (trial/basic/pro):
  - stores count
  - feed upload
  - autosync
  - pricing/preorder
- Configurable via env/db.

**DoD**
- Test coverage for allowed/blocked.

---

### E2. Enforce on critical endpoints
**Deliverables**
- Hard blocks on premium actions.

**Targets**
- feed upload endpoints
- autosync enable endpoints
- pricing/preorder endpoints

**DoD**
- Tests show correct HTTP (403/402 depending on your contract) and never 500.

---

## Milestone F — Ops Minimum

### F1. Backup/restore scripts
**Deliverables**
- `scripts/backup.ps1` and `scripts/restore.ps1` for Postgres.

**DoD**
- Documented commands and a “restore verification” step.

---

### F2. Upgrade procedure
**Deliverables**
- A consistent “upgrade playbook”:
  - pull
  - migrate
  - restart
  - smoke check

**DoD**
- Written in `docs/DEPLOYMENT.md` with real commands.

---

# 4) Definition of Done (Global)

A milestone is considered complete only if:
- All tests pass (`pytest -q`, `prod-gate.ps1`)
- All new endpoints follow the error contract and propagate `X-Request-ID`
- At least one doc section updated (DEPLOYMENT or feature doc)
- Journal entry appended (append-only)
- No historical migrations were edited (MIGRATIONS_POLICY)

---

# 5) Immediate Next Step (Start Now)

**Start with Milestone A (A1 + A2).**  
Reason: it makes every subsequent Kaspi task debuggable for the first client.

### Step 1: Create branch
```powershell
Set-Location D:\LLM_HUB\SmartSell
git checkout dev
git pull --ff-only
git checkout -b feat/kaspi-onboarding-logs-a1a2-v1
```

### Step 2: Work items for this branch
- Implement A1 (selftest status persistence + fast probe)
- Implement A2 (integration_events table + write events + read endpoint)
- Tests + prod-gate

### Step 3: Finish
```powershell
python -m ruff format .
python -m ruff check .
python -m pytest -q
pwsh -NoProfile -File .\scripts\prod-gate.ps1
```

---

## Appendix: Branch naming convention
- `feat/kaspi-onboarding-logs-a1a2-v1`
- `feat/kaspi-catalog-import-v2-v1`
- `feat/kaspi-feed-lifecycle-v1`
- `feat/kaspi-orders-ops-v1`
- `feat/subscription-enforcement-e2-v1`
- `feat/ops-backup-restore-v1`

---

## Appendix: Optional “Docs split” (later)
If `docs/DEPLOYMENT.md` becomes too big, split:
- `docs/KASPI_ONBOARDING.md`
- `docs/KASPI_CATALOG_IMPORT.md`
- `docs/KASPI_FEED.md`
- `docs/OPS_BACKUP_RESTORE.md`
