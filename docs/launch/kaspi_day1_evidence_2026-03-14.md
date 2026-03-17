# KASPI_DAY1_EVIDENCE_2026-03-14

- Date: 2026-03-14
- Candidate branch: dev
- Candidate commit: 52c498b
- Owner: Ақыл

## 1. Launch tenant
- Company / tenant: Dev Company
- Company ID: 1
- Merchant UID / store ID: 17319385
- Kaspi required for launch: Yes

## 2. Auth evidence
- Login status: PASS
- Token acquired: Yes
- Notes:
  - pwsh -NoProfile -File .\scripts\smoke-auth.ps1 passed
  - ME OK user_id=1 role=admin company_id=1 company_name=Dev Company kaspi_store_id=17319385

## 3. Kaspi status / health evidence
- Status endpoint checked: Indirectly validated through live sync-now execution
- Health endpoint checked: PASS
- Result: PASS
- Notes:
  - /api/v1/health -> 200
  - /ready -> 200
  - sync/now returned HTTP 200 on both calls

## 4. Kaspi operational path evidence
- Operational path selected: scripts/smoke-kaspi-sync-now.ps1
- Result: PASS
- Notes:
  - First call returned HTTP 200 with overall status=partial
  - Second call returned HTTP 200 with overall status=partial
  - orders_sync.ok = true
  - orders_sync.status = success
  - outbound orders window is clamped to allowed 14 days
  - window_truncated = true
  - goods_import_status = FINISHED
  - goods_import_result.ok = true
  - goods_import_result.status = noop
  - offers_feed_result.ok = true
  - No Kaspi creationDate max [14] error remains

## 5. Final verdict
- Kaspi day-1 status: PASS
- Blocking issue:
  - None confirmed in this evidence run
- Can launch proceed with Kaspi requirement: Yes

## 6. Important note
- Overall sync/now response still shows status=partial because goods import completed as 
oop, not because of a failing orders sync path.
- Current evidence confirms the critical day-1 Kaspi orders path is operational and no longer blocked by invalid date-window construction.
