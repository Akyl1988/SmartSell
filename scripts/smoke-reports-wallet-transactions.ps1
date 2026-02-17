<#
Smoke: wallet transactions CSV report

Usage:
  pwsh -NoProfile -File .\scripts\smoke-reports-wallet-transactions.ps1 -BaseUrl http://127.0.0.1:8000 -Identifier store@local -Password admin
  pwsh -NoProfile -File .\scripts\smoke-reports-wallet-transactions.ps1 -CompanyId 1001 -Limit 50

Env:
  SMARTSELL_BASE_URL, STORE_IDENTIFIER, STORE_PASSWORD, PLATFORM_IDENTIFIER, PLATFORM_PASSWORD, SMARTSELL_PLATFORM_*, COMPANY_ID, LIMIT
#>

param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL,
  [string]$Identifier = $env:STORE_IDENTIFIER,
  [string]$Password = $env:STORE_PASSWORD,
  [int]$CompanyId = $(if ($env:COMPANY_ID) { [int]$env:COMPANY_ID } else { 0 }),
  [int]$Limit = $(if ($env:LIMIT) { [int]$env:LIMIT } else { 500 }),
  [string]$DateFrom = $env:DATE_FROM,
  [string]$DateTo = $env:DATE_TO
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

function Fail([string]$msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }
function Ok([string]$msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Info([string]$msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

$scriptDir = Get-ScriptDir
. (Join-Path $scriptDir "_smoke-lib.ps1")

$headers = Get-SmokeAuthHeader -BaseUrl $BaseUrl
Ok "Auth header ready"

$query = @{}
if ($Limit -gt 0) { $query["limit"] = $Limit }
if ($DateFrom) { $query["date_from"] = $DateFrom }
if ($DateTo) { $query["date_to"] = $DateTo }
if ($CompanyId -gt 0) { $query["companyId"] = $CompanyId }

$qs = ($query.GetEnumerator() | Sort-Object Name | ForEach-Object {
  $k = $_.Name
  $v = [uri]::EscapeDataString([string]$_.Value)
  "$k=$v"
}) -join "&"

$url = "$BaseUrl/api/v1/reports/wallet/transactions.csv"
if ($qs) { $url = "$($url)?$qs" }

Info "GET $url"
$resp = Invoke-SmartsellApi -Method "GET" -Url $url -TimeoutSec 20 -Headers $headers
if ($resp.StatusCode -ne 200) {
  $body = $resp.Body
  $text = if ($body -is [string]) { $body } elseif ($body) { $body | ConvertTo-Json -Depth 10 } else { "" }
  Fail "request failed: status=$($resp.StatusCode) body=$text"
}

$bodyText = $resp.Body
if ($bodyText -isnot [string]) { $bodyText = $bodyText | ConvertTo-Json -Depth 10 }
$lines = $bodyText -split "`n"
Write-Host "CSV preview:"
$lines | Select-Object -First 5 | ForEach-Object { Write-Host $_ }

Ok "DONE"
