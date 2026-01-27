param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$OutDir  = "docs/smoke"
)

$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$p) {
  if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

function Get-Json([string]$url) {
  return Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 15
}

Ensure-Dir $OutDir

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$outJson = Join-Path $OutDir ("openapi-{0}.json" -f $ts)
$outTxt  = Join-Path $OutDir ("openapi-smoke-{0}.txt" -f $ts)

$openapiUrl = "{0}/openapi.json" -f $BaseUrl
$spec = Get-Json $openapiUrl

$spec | ConvertTo-Json -Depth 100 | Out-File -FilePath $outJson -Encoding utf8

$paths = @()
if ($null -ne $spec.paths) {
  $paths = $spec.paths.PSObject.Properties.Name | Sort-Object
}

$critical = @(
  "/api/v1/health",
  "/health",
  "/api/v1/auth/health",
  "/api/v1/wallet/health",
  "/api/v1/users/me",
  "/api/v1/auth/me",
  "/api/v1/kaspi/status"
)

$missing = @()
$present = @()

foreach ($p in $critical) {
  if ($paths -contains $p) { $present += $p } else { $missing += $p }
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("OpenAPI Smoke Report ($ts)")
$lines.Add("BaseUrl: $BaseUrl")
$lines.Add("OpenAPI: $openapiUrl")
$lines.Add("Saved OpenAPI JSON: $outJson")
$lines.Add("")
$lines.Add("Total paths: " + $paths.Count)
$lines.Add("")
$lines.Add("Critical paths present:")
if ($present.Count -eq 0) { $lines.Add(" - (none)") } else { $present | ForEach-Object { $lines.Add(" - " + $_) } }
$lines.Add("")
$lines.Add("Critical paths missing:")
if ($missing.Count -eq 0) { $lines.Add(" - (none)") } else { $missing | ForEach-Object { $lines.Add(" - " + $_) } }
$lines.Add("")
$lines.Add("First 80 paths (sorted):")
($paths | Select-Object -First 80) | ForEach-Object { $lines.Add(" - " + $_) }

$lines | Out-File -FilePath $outTxt -Encoding utf8

Write-Host ""
Write-Host ("Saved OpenAPI: {0}" -f $outJson)
Write-Host ("Saved Report: {0}" -f $outTxt)
Write-Host ""

if ($missing.Count -gt 0) {
  Write-Host "MISSING critical paths:"
  $missing | ForEach-Object { Write-Host (" - " + $_) }
  exit 2
}

Write-Host "OK: all critical paths found in OpenAPI."
exit 0
