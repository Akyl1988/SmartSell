<#
Smoke: order items CSV report

Usage:
  pwsh -NoProfile -File .\scripts\smoke-reports-order-items.ps1 -BaseUrl http://127.0.0.1:8000 -Limit 5
  pwsh -NoProfile -File .\scripts\smoke-reports-order-items.ps1 -CompanyId 1001 -Limit 10

Env:
  SMARTSELL_BASE_URL, COMPANY_ID, LIMIT
#>

param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [int]$Limit = 5,
  [int]$CompanyId = $(if ($env:COMPANY_ID) { [int]$env:COMPANY_ID } else { 0 })
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ScriptDir {
  if ($PSScriptRoot) { return $PSScriptRoot }
  if ($MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    return Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  return (Get-Location).Path
}

function Fail([string]$msg) { Write-Host "[ERR] $msg" -ForegroundColor Red; exit 1 }
function Ok([string]$msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Info([string]$msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

$scriptDir = Get-ScriptDir
. (Join-Path $scriptDir "_smoke-lib.ps1")

$headers = Get-SmokeAuthHeader -BaseUrl $BaseUrl
Ok "Auth header ready"

$query = @{}
if ($Limit -gt 0) { $query["limit"] = $Limit }
if ($CompanyId -gt 0) { $query["companyId"] = $CompanyId }

$qs = ($query.GetEnumerator() | Sort-Object Name | ForEach-Object {
  $k = $_.Name
  $v = [uri]::EscapeDataString([string]$_.Value)
  "$k=$v"
}) -join "&"

$url = "$BaseUrl/api/v1/reports/order_items.csv"
if ($qs) { $url = "$($url)?$qs" }

Info "GET $url"
$resp = Invoke-SmartsellApi -Method "GET" -Url $url -TimeoutSec 20 -Headers $headers
if ($resp.StatusCode -ne 200) {
  $body = $resp.Body
  $text = if ($body -is [string]) { $body } elseif ($body) { $body | ConvertTo-Json -Depth 10 } else { "" }
  Fail "request failed: status=$($resp.StatusCode) body=$text"
}

if ($resp.Body -isnot [string]) {
  $text = $resp.Body | ConvertTo-Json -Depth 10
  Fail "unexpected json response: $text"
}

$lines = $resp.Body -split "`n"
Write-Host "CSV preview:"
$lines | Select-Object -First 10 | ForEach-Object { Write-Host $_ }

Ok "DONE"
