<#
Smoke: campaigns E2E (enqueue -> process -> queue visibility)

Usage:
  pwsh -NoProfile -File .\scripts\smoke-campaigns-e2e.ps1 -BaseUrl http://127.0.0.1:8000 -Email admin@local -Password admin -CompanyId 1
  pwsh -NoProfile -File .\scripts\smoke-campaigns-e2e.ps1 -Email admin@local -Password admin -CompanyId 1 -Limit 20 -AllowFailure

Env:
  BASE_URL, EMAIL, PASSWORD, COMPANY_ID, LIMIT
#>

param(
  [string]$BaseUrl = $env:BASE_URL,
  [string]$Email = $env:EMAIL,
  [string]$Password = $env:PASSWORD,
  [int]$CompanyId = $(if ($env:COMPANY_ID) { [int]$env:COMPANY_ID } else { 0 }),
  [int]$Limit = $(if ($env:LIMIT) { [int]$env:LIMIT } else { 50 }),
  [switch]$AllowFailure
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

function Print-Queue([object]$Items, [int]$Max) {
  if (-not $Items) { return }
  $Items | Select-Object -First $Max | Select-Object id, company_id, processing_status, attempts, last_error, queued_at, started_at, finished_at, failed_at, request_id | Format-Table -AutoSize
}

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

$scriptDir = Get-ScriptDir
. (Join-Path $scriptDir "_smoke-lib.ps1")

if ([string]::IsNullOrWhiteSpace($Email) -or [string]::IsNullOrWhiteSpace($Password)) {
  Fail "Missing credentials. Provide -Email and -Password or set EMAIL/PASSWORD."
}
if ($CompanyId -le 0) {
  Fail "CompanyId is required. Provide -CompanyId or set COMPANY_ID."
}

Info "Login"
$tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Email -Password $Password
$access = $tokens.access
$refresh = $tokens.refresh
Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh -BaseUrl $BaseUrl
Ok ("Token loaded: " + (Mask-Secret $access))

$campaignId = $null
$seedUrl = "$BaseUrl/api/v1/admin/dev/seed/campaign_due?company_id=$CompanyId"
Info "Seed campaign (dev/test endpoint): $seedUrl"
$seedResp = Invoke-SmartsellApi -Method "POST" -Url $seedUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
if ($seedResp.StatusCode -ge 200 -and $seedResp.StatusCode -lt 300) {
  $campaignId = $seedResp.Body.campaign_id
  if ($campaignId) { Ok "Seeded campaign_id=$campaignId" }
} else {
  Info "Seed endpoint not available; continuing without seed"
}

if (-not $campaignId) {
  $createUrl = "$BaseUrl/api/v1/campaigns/"
  $payload = @{
    title = "Smoke E2E " + ([guid]::NewGuid().ToString("N").Substring(0, 8))
    description = "smoke-e2e"
    messages = @(
      @{ recipient = "smoke@example.com"; content = "Smoke message"; status = "pending"; channel = "email" }
    )
    tags = @("smoke")
    active = $true
  }
  Info "Create campaign via /api/v1/campaigns/ (store admin only)"
  $createResp = Invoke-SmartsellApi -Method "POST" -Url $createUrl -Body $payload -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
  if ($createResp.StatusCode -ge 200 -and $createResp.StatusCode -lt 300) {
    $campaignId = $createResp.Body.id
    if ($campaignId) { Ok "Created campaign_id=$campaignId" }
  } else {
    Info "Create campaign not allowed for current user; continuing without create"
  }
}

$runUrl = "$BaseUrl/api/v1/admin/tasks/campaigns/run?company_id=$CompanyId"
Info "POST $runUrl"
$runResp = Invoke-SmartsellApi -Method "POST" -Url $runUrl -Body @{} -TimeoutSec 30 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $runResp -Allowed @(200) -Label "campaigns run"
Write-Host ("run response: " + ($runResp.Body | ConvertTo-Json -Depth 20))

$queueUrl = "$BaseUrl/api/v1/admin/campaigns/queue?companyId=$CompanyId&limit=$Limit"
Info "GET $queueUrl"
$queueResp = Invoke-SmartsellApi -Method "GET" -Url $queueUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
Assert-Status -Resp $queueResp -Allowed @(200) -Label "queue list"
$items = $queueResp.Body
Print-Queue -Items $items -Max $Limit

if (-not $campaignId -and $items -and $items.Count -gt 0) {
  $campaignId = $items[0].id
  if ($campaignId) { Info "Using campaign_id=$campaignId from queue" }
}

if (-not $campaignId) {
  Write-Host "no campaigns"
  exit 0
}

function Get-CampaignStatus([int]$Id) {
  $url = "$BaseUrl/api/v1/admin/campaigns/$Id"
  $resp = Invoke-SmartsellApi -Method "GET" -Url $url -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
  return $resp
}

$finalResp = $null
for ($i = 0; $i -lt 6; $i++) {
  Start-Sleep -Seconds 1
  $finalResp = Get-CampaignStatus -Id $campaignId
  if ($finalResp.StatusCode -ge 200 -and $finalResp.StatusCode -lt 300) {
    $ps = $finalResp.Body.processing_status
    if ($ps -in @("done", "failed")) { break }
  }
}

if ($finalResp -and $finalResp.StatusCode -ge 200 -and $finalResp.StatusCode -lt 300) {
  $status = $finalResp.Body.processing_status
  $lastError = $finalResp.Body.last_error
  Info "Final status: $status last_error=$lastError"

  if ($status -eq "failed" -and $lastError -eq "max_attempts_exceeded") {
    $requeueUrl = "$BaseUrl/api/v1/admin/campaigns/$campaignId/requeue?force=false"
    Info "Requeue due to max_attempts_exceeded: $requeueUrl"
    $requeueResp = Invoke-SmartsellApi -Method "POST" -Url $requeueUrl -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
    Assert-Status -Resp $requeueResp -Allowed @(200, 409) -Label "requeue"

    Info "Re-run campaigns task"
    $rerunResp = Invoke-SmartsellApi -Method "POST" -Url $runUrl -Body @{} -TimeoutSec 30 -AccessToken $access -RefreshToken $refresh -Identifier $Email -Password $Password
    Assert-Status -Resp $rerunResp -Allowed @(200) -Label "campaigns run (retry)"

    $finalResp = Get-CampaignStatus -Id $campaignId
    $status = $finalResp.Body.processing_status
    $lastError = $finalResp.Body.last_error
    Info "Final status after retry: $status last_error=$lastError"
  }

  if ($status -eq "done") {
    Ok "DONE"
    exit 0
  }
}

if ($AllowFailure) {
  Write-Host "AllowFailure enabled; exiting 0"
  exit 0
}

Fail "Campaign did not reach done status"
