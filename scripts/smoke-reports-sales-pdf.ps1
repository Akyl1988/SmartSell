<#
Smoke: sales PDF report

Usage:
  pwsh -NoProfile -File .\scripts\smoke-reports-sales-pdf.ps1 -BaseUrl http://127.0.0.1:8000 -DateFrom 2026-02-01 -DateTo 2026-02-15
  pwsh -NoProfile -File .\scripts\smoke-reports-sales-pdf.ps1 -CompanyId 1001 -Limit 100

Env:
  SMARTSELL_BASE_URL, COMPANY_ID, LIMIT, DATE_FROM, DATE_TO
#>

param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [int]$Limit = 100,
  [int]$CompanyId = $(if ($env:COMPANY_ID) { [int]$env:COMPANY_ID } else { 0 }),
  [string]$DateFrom = $env:DATE_FROM,
  [string]$DateTo = $env:DATE_TO,
  [switch]$SaveToTemp
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
if ($DateFrom) { $query["date_from"] = $DateFrom }
if ($DateTo) { $query["date_to"] = $DateTo }
if ($CompanyId -gt 0) { $query["companyId"] = $CompanyId }

$qs = ($query.GetEnumerator() | Sort-Object Name | ForEach-Object {
  $k = $_.Name
  $v = [uri]::EscapeDataString([string]$_.Value)
  "$k=$v"
}) -join "&"

$url = "$BaseUrl/api/v1/reports/sales.pdf"
if ($qs) { $url = "$($url)?$qs" }

Info "GET $url"
$resp = Invoke-SmartsellApi -Method "GET" -Url $url -TimeoutSec 25 -Headers $headers
if ($resp.StatusCode -ne 200) {
  $body = $resp.Body
  $text = if ($body -is [string]) { $body } elseif ($body) { $body | ConvertTo-Json -Depth 10 } else { "" }
  Fail "request failed: status=$($resp.StatusCode) body=$text"
}

$ct = ""
if ($resp.Headers) {
  $ct = [string](@($resp.Headers["Content-Type"])[0])
  if (-not $ct) { $ct = [string](@($resp.Headers["content-type"])[0]) }
}
if ($ct -notmatch "application/pdf") {
  Fail "unexpected content-type: $ct"
}

$body = $resp.Body
if (-not $body) { Fail "empty PDF body" }

[byte[]]$bytes = $null
if ($body -is [byte[]]) {
  $bytes = $body
} else {
  $bytes = [System.Text.Encoding]::Latin1.GetBytes([string]$body)
}

$preview = ($bytes | Select-Object -First 8 | ForEach-Object { $_.ToString("X2") }) -join " "
Write-Host "PDF bytes: $preview"

if ($SaveToTemp.IsPresent) {
  $tmp = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), "pdf")
  [System.IO.File]::WriteAllBytes($tmp, $bytes)
  Ok "Saved to $tmp"
}

Ok "DONE"
