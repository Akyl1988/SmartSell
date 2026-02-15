# Reports CSV Runbook

This runbook covers CSV export endpoints under /api/v1/reports.

## Endpoints

- GET /api/v1/reports/wallet/transactions.csv
- GET /api/v1/reports/orders.csv
- GET /api/v1/reports/order_items.csv

All endpoints accept:
- limit (default 500, max 5000)
- date_from (optional ISO 8601 datetime)
- date_to (optional ISO 8601 datetime)
- companyId (platform admins only)

## PowerShell examples

Use Invoke-WebRequest and decode UTF-8 explicitly:

```
$base = "http://127.0.0.1:8000"
$headers = @{ Authorization = "Bearer <access_token>" }
$response = Invoke-WebRequest -Method GET -Uri "$base/api/v1/reports/orders.csv?limit=5" -Headers $headers
$content = [System.Text.Encoding]::UTF8.GetString($response.Content)
$content | Select-Object -First 10
```

With companyId:

```
$response = Invoke-WebRequest -Method GET -Uri "$base/api/v1/reports/order_items.csv?limit=5&companyId=1001" -Headers $headers
[System.Text.Encoding]::UTF8.GetString($response.Content) | Select-Object -First 10
```

## curl examples

```
curl -s -H "Authorization: Bearer <access_token>" "$base/api/v1/reports/wallet/transactions.csv?limit=5" | head -n 10
curl -s -H "Authorization: Bearer <access_token>" "$base/api/v1/reports/orders.csv?limit=5" | head -n 10
curl -s -H "Authorization: Bearer <access_token>" "$base/api/v1/reports/order_items.csv?limit=5" | head -n 10
```

## Common errors

- TOKEN_EXPIRED: access token expired. Run scripts/smoke-auth.ps1 to refresh cached tokens.
- SESSION_TERMINATED: refresh token revoked or expired. Run scripts/smoke-auth.ps1 to login again.
