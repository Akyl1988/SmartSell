<#
Smoke: platform_admin campaign queue ops

Usage:
  pwsh -NoProfile -File .\scripts\smoke-admin-campaign-queue.ps1 -BaseUrl http://127.0.0.1:8000 -Email platform@local -Password admin
  pwsh -NoProfile -File .\scripts\smoke-admin-campaign-queue.ps1 -Email platform@local -Password admin -CompanyId 123 -Limit 10

Env:
  BASE_URL, PLATFORM_IDENTIFIER, PLATFORM_PASSWORD, SMARTSELL_PLATFORM_*, EMAIL, PASSWORD, COMPANY_ID, LIMIT
#>

param(
  [string]$BaseUrl = $env:BASE_URL,
  [string]$Email = $(if ($env:PLATFORM_IDENTIFIER) { $env:PLATFORM_IDENTIFIER } else { $env:EMAIL }),
  [string]$Password = $(if ($env:PLATFORM_PASSWORD) { $env:PLATFORM_PASSWORD } else { $env:PASSWORD }),
  [int]$CompanyId = $(if ($env:COMPANY_ID) { [int]$env:COMPANY_ID } else { 0 }),
  [int]$Limit = $(if ($env:LIMIT) { [int]$env:LIMIT } else { 20 })
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

if ([string]::IsNullOrWhiteSpace($Email)) { $Email = $env:SMARTSELL_PLATFORM_IDENTIFIER }
if ([string]::IsNullOrWhiteSpace($Password)) { $Password = $env:SMARTSELL_PLATFORM_PASSWORD }
if ([string]::IsNullOrWhiteSpace($Email)) { $Email = $env:SMARTSELL_PLATFORM_ADMIN_IDENTIFIER }
if ([string]::IsNullOrWhiteSpace($Password)) { $Password = $env:SMARTSELL_PLATFORM_ADMIN_PASSWORD }

if ([string]::IsNullOrWhiteSpace($Email) -or [string]::IsNullOrWhiteSpace($Password)) {
  Fail "platform_admin creds required. Set PLATFORM_IDENTIFIER/PLATFORM_PASSWORD (or SMARTSELL_PLATFORM_*)."
}

Info "Login"
$tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Email -Password $Password
$access = $tokens.access
$refresh = $tokens.refresh
Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh -BaseUrl $BaseUrl
Ok ("Token loaded: " + (Mask-Secret $access))

$query = "limit=$Limit"
if ($CompanyId -gt 0) { $query = "$query&companyId=$CompanyId" }
$queueUrl = "$BaseUrl/api/v1/admin/campaigns/queue?$query"
Info "GET $queueUrl"
$queueResp = Invoke-SmartsellApi -Method "GET" -Url $queueUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $queueResp -Allowed @(200) -Label "queue list"

$items = $queueResp.Body
if (-not $items -or $items.Count -eq 0) {
  Write-Host "no campaigns"
  exit 0
}

Info "Queue items (first $Limit):"
$items | Select-Object -First $Limit | Select-Object id, company_id, processing_status, attempts, last_error, request_id | Format-Table -AutoSize

$campaignId = $items[0].id
if (-not $campaignId) {
  Write-Host "no campaigns"
  exit 0
}

$cancelUrl = "$BaseUrl/api/v1/admin/campaigns/$campaignId/cancel"
Info "POST $cancelUrl"
$cancelResp = Invoke-SmartsellApi -Method "POST" -Url $cancelUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $cancelResp -Allowed @(200, 409) -Label "cancel"
Write-Host ("cancel response: " + ($cancelResp.Body | ConvertTo-Json -Depth 20))

$requeueUrl = "$BaseUrl/api/v1/admin/campaigns/$campaignId/requeue?force=false"
Info "POST $requeueUrl"
$requeueResp = Invoke-SmartsellApi -Method "POST" -Url $requeueUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $requeueResp -Allowed @(200, 409) -Label "requeue force=false"
Write-Host ("requeue (force=false) response: " + ($requeueResp.Body | ConvertTo-Json -Depth 20))

$requeueForceUrl = "$BaseUrl/api/v1/admin/campaigns/$campaignId/requeue?force=true"
Info "POST $requeueForceUrl"
$requeueForceResp = Invoke-SmartsellApi -Method "POST" -Url $requeueForceUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $requeueForceResp -Allowed @(200) -Label "requeue force=true"
Write-Host ("requeue (force=true) response: " + ($requeueForceResp.Body | ConvertTo-Json -Depth 20))

Info "GET $queueUrl"
$queueResp2 = Invoke-SmartsellApi -Method "GET" -Url $queueUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $queueResp2 -Allowed @(200) -Label "queue list (after)"
$items2 = $queueResp2.Body
if ($items2) {
  $items2 | Select-Object -First $Limit | Select-Object id, company_id, processing_status, attempts, last_error, request_id | Format-Table -AutoSize
}

Ok "DONE"
