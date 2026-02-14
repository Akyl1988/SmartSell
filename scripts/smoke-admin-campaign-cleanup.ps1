<#
Smoke: admin campaign cleanup

Usage:
  pwsh -NoProfile -File .\scripts\smoke-admin-campaign-cleanup.ps1 -BaseUrl http://127.0.0.1:8000 -CompanyId 1

Env:
  SMARTSELL_BASE_URL, ADMIN_IDENTIFIER, ADMIN_PASSWORD, SMARTSELL_IDENTIFIER, SMARTSELL_PASSWORD, COMPANY_ID
#>

param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL,
  [Alias("AdminIdentifier")][string]$Identifier = $env:ADMIN_IDENTIFIER,
  [Alias("AdminPassword")][string]$Password = $env:ADMIN_PASSWORD,
  [int]$CompanyId = $(if ($env:COMPANY_ID) { [int]$env:COMPANY_ID } else { 0 }),
  [int]$DoneDays = 14,
  [int]$FailedDays = 30,
  [int]$Limit = 500
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

function Assert-Status([object]$Resp, [int[]]$Allowed, [string]$Label) {
  $status = [int]($Resp.StatusCode ?? 0)
  if ($Allowed -notcontains $status) {
    $body = $Resp.Body
    $text = if ($body -is [string]) { $body } elseif ($body) { $body | ConvertTo-Json -Depth 20 } else { "" }
    Fail "$Label failed: status=$status body=$text"
  }
}

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

$scriptDir = Get-ScriptDir
. (Join-Path $scriptDir "_smoke-lib.ps1")
if ($CompanyId -le 0) {
  Fail "CompanyId is required. Provide -CompanyId or set COMPANY_ID."
}
if ($Limit -le 0) {
  Fail "Limit must be >= 1"
}

$headers = Get-SmokeAuthHeader -BaseUrl $BaseUrl
Ok "Auth header ready"

$queueLimit = [Math]::Min($Limit, 200)
$queueUrl = "$BaseUrl/api/v1/admin/campaigns/queue?companyId=$CompanyId&limit=$queueLimit"
Info "GET $queueUrl"
$queueResp = Invoke-SmartsellApi -Method "GET" -Url $queueUrl -TimeoutSec 20 -Headers $headers
Assert-Status -Resp $queueResp -Allowed @(200) -Label "queue list"
$items = $queueResp.Body

$cutoffDone = (Get-Date).ToUniversalTime().AddDays(-$DoneDays)
$oldDoneBefore = 0
if ($items) {
  foreach ($item in $items) {
    if ($item.processing_status -ne "done") { continue }
    if (-not $item.finished_at) { continue }
    try {
      $finishedAt = [datetime]::Parse($item.finished_at).ToUniversalTime()
      if ($finishedAt -lt $cutoffDone) { $oldDoneBefore += 1 }
    } catch {
      continue
    }
  }
}
Info "Old DONE before cleanup: $oldDoneBefore"

$cleanupUrl = "$BaseUrl/api/v1/admin/tasks/campaigns/cleanup/run?done_days=$DoneDays&failed_days=$FailedDays&limit=$Limit"
Info "POST $cleanupUrl"
$cleanupResp = Invoke-SmartsellApi -Method "POST" -Url $cleanupUrl -TimeoutSec 30 -Headers $headers
Assert-Status -Resp $cleanupResp -Allowed @(200) -Label "campaigns cleanup"
Write-Host ("cleanup response: " + ($cleanupResp.Body | ConvertTo-Json -Depth 20))

Info "GET $queueUrl"
$queueResp2 = Invoke-SmartsellApi -Method "GET" -Url $queueUrl -TimeoutSec 20 -Headers $headers
Assert-Status -Resp $queueResp2 -Allowed @(200) -Label "queue list (after)"
$items2 = $queueResp2.Body

$oldDoneAfter = 0
if ($items2) {
  foreach ($item in $items2) {
    if ($item.processing_status -ne "done") { continue }
    if (-not $item.finished_at) { continue }
    try {
      $finishedAt = [datetime]::Parse($item.finished_at).ToUniversalTime()
      if ($finishedAt -lt $cutoffDone) { $oldDoneAfter += 1 }
    } catch {
      continue
    }
  }
}
Info "Old DONE after cleanup: $oldDoneAfter"

if ($oldDoneBefore -gt 0 -and $oldDoneAfter -ge $oldDoneBefore) {
  Fail "Expected old DONE campaigns to decrease; before=$oldDoneBefore after=$oldDoneAfter"
}
if ($oldDoneBefore -eq 0) {
  Ok "No old DONE campaigns to clean up"
} else {
  Ok "Old DONE campaigns decreased"
}

Ok "DONE"
