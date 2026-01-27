param(
  [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Assert-PathExists([object]$spec, [string]$path, [string]$method) {
  $p = $spec.paths.$path
  if ($null -eq $p) {
    throw "MISSING path: $path"
  }
  $m = $p.$method
  if ($null -eq $m) {
    throw "MISSING method: $method $path"
  }
}

Write-Host "OPENAPI $BaseUrl/openapi.json"
$spec = Invoke-RestMethod -Uri "$BaseUrl/openapi.json" -TimeoutSec 20

# must-have routes (core v1)
Assert-PathExists $spec "/api/v1/auth/login" "post"
Assert-PathExists $spec "/api/v1/auth/me" "get"
Assert-PathExists $spec "/api/v1/auth/refresh" "post"
Assert-PathExists $spec "/api/v1/auth/logout" "post"

# legacy: may be intentionally excluded from schema => warn only
if ($spec.paths."/api/auth/me" -and $spec.paths."/api/auth/me".get) {
  Write-Host "OK   legacy /api/auth/me is in schema"
} else {
  Write-Host "WARN legacy /api/auth/me is not in schema (covered by smoke-auth.ps1)"
}

# sanity endpoints (optional)
if ($spec.paths."/api/v1/health") { Write-Host "OK   /api/v1/health" } else { Write-Host "WARN /api/v1/health not found" }
if ($spec.paths."/api/v1/wallet/health") { Write-Host "OK   /api/v1/wallet/health" } else { Write-Host "WARN /api/v1/wallet/health not found" }

$pathCount = ($spec.paths.PSObject.Properties | Measure-Object).Count
Write-Host "DONE OK paths=$pathCount"
