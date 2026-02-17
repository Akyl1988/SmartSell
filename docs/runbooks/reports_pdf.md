# Reports PDF Runbook

## Endpoints

- GET /api/v1/reports/orders.pdf
- GET /api/v1/reports/sales.pdf

## Parameters

Common:
- companyId (int, optional): Tenant override for platform_admins only.
- date_from (string, optional):
  - orders.pdf expects ISO 8601 datetime (e.g., 2026-02-01T00:00:00Z)
  - sales.pdf expects YYYY-MM-DD
- date_to (string, optional):
  - orders.pdf expects ISO 8601 datetime
  - sales.pdf expects YYYY-MM-DD
- limit (int, optional): Orders limit. Sales keeps it for parity but does not apply it to aggregates.

## RBAC

- store_admin / store_manager: Only their own company. Passing a different companyId returns 404.
- platform_admin / superuser: Can request any tenant with companyId.

## Examples

### curl

Orders PDF (store_admin):

```bash
curl -sS -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8000/api/v1/reports/orders.pdf?limit=50&date_from=2026-02-01T00:00:00Z&date_to=2026-02-15T23:59:59Z" \
  -o orders.pdf
```

Sales PDF (platform_admin + tenant override):

```bash
curl -sS -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8000/api/v1/reports/sales.pdf?companyId=1001&date_from=2026-02-01&date_to=2026-02-15" \
  -o sales.pdf
```

### PowerShell

```powershell
Invoke-RestMethod -Method GET `
  -Uri "http://127.0.0.1:8000/api/v1/reports/orders.pdf?limit=50" `
  -Headers @{ Authorization = "Bearer <token>" } `
  -OutFile "orders.pdf"
```

```powershell
Invoke-RestMethod -Method GET `
  -Uri "http://127.0.0.1:8000/api/v1/reports/sales.pdf?date_from=2026-02-01&date_to=2026-02-15" `
  -Headers @{ Authorization = "Bearer <token>" } `
  -OutFile "sales.pdf"
```

## Smoke Scripts

```powershell
pwsh -NoProfile -File .\scripts\smoke-reports-orders-pdf.ps1 -BaseUrl http://127.0.0.1:8000 -Limit 5
pwsh -NoProfile -File .\scripts\smoke-reports-sales-pdf.ps1 -BaseUrl http://127.0.0.1:8000 -DateFrom 2026-02-01 -DateTo 2026-02-15
```

Notes:
- Smoke scripts use cached tokens from scripts/.smoke-cache.json and auto-refresh when needed.
- Use -CompanyId for platform_admin tenant override.
