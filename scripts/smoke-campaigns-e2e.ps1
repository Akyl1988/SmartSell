param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL
)

$ErrorActionPreference = "Stop"

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

function Get-ScriptDir {
  if ($PSScriptRoot) { return $PSScriptRoot }
  if ($MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    return Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  return (Get-Location).Path
}

$ScriptDir = Get-ScriptDir
. (Join-Path $ScriptDir "_smoke-lib.ps1")

Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null

$authHeaders = Get-SmokeAuthHeader -BaseUrl $BaseUrl
$accessToken = $null
if ($authHeaders -and $authHeaders.Authorization) {
  $accessToken = ([string]$authHeaders.Authorization).Replace("Bearer ", "").Trim()
}

function Assert-Ok {
  param(
    [object]$Resp,
    [string]$Action
  )
  if (-not $Resp) { throw "$Action failed: no response" }
  $status = $Resp.StatusCode
  if ($status -lt 200 -or $status -ge 300) {
    $bodyText = $null
    try { $bodyText = $Resp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $Resp.Body }
    throw "$Action failed: status=$status body=$bodyText"
  }
  return $Resp.Body
}

function Get-RespItems {
  param([object]$Body)
  if (-not $Body) { return @() }
  if ($Body.PSObject.Properties["items"]) { return @($Body.items) }
  if ($Body.PSObject.Properties["data"]) { return @($Body.data) }
  return @()
}

function Get-Id {
  param([object]$Obj)
  if (-not $Obj) { return $null }
  $prop = $Obj.PSObject.Properties["id"]
  if ($prop) { return $prop.Value }
  return $null
}

function Get-PropValue {
  param(
    [object]$Obj,
    [string]$Name
  )
  if (-not $Obj) { return $null }
  $prop = $Obj.PSObject.Properties[$Name]
  if ($prop) { return $prop.Value }
  return $null
}

function Invoke-Api {
  param(
    [string]$Method,
    [string]$Url,
    [object]$Body = $null,
    [int]$TimeoutSec = 20
  )
  return Invoke-SmartsellApi -Method $Method -Url $Url -Body $Body -TimeoutSec $TimeoutSec -AccessToken $accessToken
}

function Format-RespBrief {
  param([object]$Resp)
  if (-not $Resp) { return "status=(no response) body=(none)" }
  $status = $Resp.StatusCode
  $bodyText = $null
  try { $bodyText = $Resp.Body | ConvertTo-Json -Depth 8 } catch { $bodyText = $Resp.Body }
  return "status=$status body=$bodyText"
}

function Try-Get-Profile {
  $meResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/auth/me"
  if ($meResp.StatusCode -ge 200 -and $meResp.StatusCode -lt 300) { return $meResp.Body }
  return $null
}

function Try-Seed-DbCampaign {
  param([int]$CompanyId)
  $seedUrl = "$BaseUrl/api/v1/admin/dev/seed/campaign_due"
  if ($CompanyId -gt 0) { $seedUrl = "${seedUrl}?company_id=$CompanyId" }
  $seedResp = Invoke-Api -Method "POST" -Url $seedUrl -TimeoutSec 60
  if ($seedResp.StatusCode -eq 403 -or $seedResp.StatusCode -eq 404) {
    Write-Host "[WARN] Seed campaign endpoint not available for this token (403/404)"
    return $null
  }
  $seedBody = Assert-Ok -Resp $seedResp -Action "Seed campaign"
  return (Get-PropValue -Obj $seedBody -Name "campaign_id")
}

function Get-CampaignState {
  param([int]$CampaignId)
  $resp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/campaigns/$CampaignId"
  if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) { return @{ source = "store"; body = $resp.Body } }

  $adminResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/admin/campaigns/$CampaignId"
  if ($adminResp.StatusCode -ge 200 -and $adminResp.StatusCode -lt 300) { return @{ source = "admin"; body = $adminResp.Body } }

  return $null
}

$runId = ([guid]::NewGuid().ToString("N")).Substring(0, 8)
$title = "Smoke Campaign $runId"
$allowSkip = $false
$allowSkipRaw = ($env:SMARTSELL_SMOKE_ALLOW_SKIP ?? "").ToString().Trim().ToLower()
if ($allowSkipRaw -in @("1", "true", "yes", "on")) { $allowSkip = $true }

$campaignPayload = @{
  title = $title
  description = "Smoke campaigns run $runId"
  messages = @(
    @{
      recipient = "smoke+$runId@example.com"
      content = "Smoke campaigns $runId"
      status = "pending"
      channel = "email"
    }
  )
  tags = @("smoke", "campaign")
  active = $true
}

$createResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/campaigns/" -Body $campaignPayload
if ($createResp.StatusCode -eq 409) {
  Write-Host "[WARN] Campaign title already exists; reusing existing campaign"
  $listResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/campaigns/?page=1&size=50&order=desc"
  $listBody = Assert-Ok -Resp $listResp -Action "List campaigns"
  $items = @(Get-RespItems -Body $listBody)
  $match = $items | Where-Object { $_.title -eq $title } | Select-Object -First 1
  if (-not $match) { throw "Campaign create failed and no existing campaign found" }
  $campaign = $match
} else {
  $campaign = Assert-Ok -Resp $createResp -Action "Create campaign"
}

$campaignId = Get-Id -Obj $campaign
if (-not $campaignId) { throw "Missing campaign id" }

$stateBefore = Get-CampaignState -CampaignId $campaignId
$beforeQueuedAt = $null
$beforeAttempts = $null
if ($stateBefore) {
  $beforeQueuedAt = Get-PropValue -Obj $stateBefore.body -Name "queued_at"
  $beforeAttempts = Get-PropValue -Obj $stateBefore.body -Name "attempts"
}

$runResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/campaigns/$campaignId/run" -TimeoutSec 60
$runBody = $null
$skipRun = $false
$seedResp = $null
if ($runResp.StatusCode -eq 404) {
  $runCode = Get-PropValue -Obj $runResp.Body -Name "code"
  if (-not $runCode) { $runCode = Get-PropValue -Obj $runResp.Body -Name "detail" }
  if ($runCode -ne "campaign_not_found") {
    $runInfo = Format-RespBrief -Resp $runResp
    throw "Campaign run endpoint returned 404 (non campaign_not_found): $runInfo"
  }

  $profile = Try-Get-Profile
  $companyId = 0
  if ($profile) { $companyId = [int](Get-PropValue -Obj $profile -Name "company_id") }
  $seedResp = Invoke-Api -Method "POST" -Url ("$BaseUrl/api/v1/admin/dev/seed/campaign_due" + ($(if ($companyId -gt 0) { "?company_id=$companyId" } else { "" }))) -TimeoutSec 60
  if ($seedResp.StatusCode -eq 403 -or $seedResp.StatusCode -eq 404) {
    Write-Host "[WARN] Seed campaign endpoint not available for this token (403/404)"
    if (-not $allowSkip) {
      $runInfo = Format-RespBrief -Resp $runResp
      $seedInfo = Format-RespBrief -Resp $seedResp
      throw "E2E impossible: campaigns storage mismatch (create uses non-DB storage) and admin seed not available for this token. run=$runInfo seed=$seedInfo"
    }
    Write-Host "[WARN] SMARTSELL_SMOKE_ALLOW_SKIP=1; skipping campaigns run"
    exit 0
  }
  $seedBody = Assert-Ok -Resp $seedResp -Action "Seed campaign"
  $seededId = Get-PropValue -Obj $seedBody -Name "campaign_id"
  if ($seededId) {
    $campaignId = [int]$seededId
    $runResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/campaigns/$campaignId/run" -TimeoutSec 60
  }
}

if ($runResp.StatusCode -eq 404) {
  $runInfo = Format-RespBrief -Resp $runResp
  throw "Campaign run failed: $runInfo"
} elseif ($runResp.StatusCode -eq 409) {
  Write-Host "[WARN] Campaign run already queued"
  $runBody = $runResp.Body
} else {
  $runBody = Assert-Ok -Resp $runResp -Action "Run campaign"
}

$runRequestId = Get-PropValue -Obj $runBody -Name "request_id"
if (-not $runRequestId) { $runRequestId = Get-PropValue -Obj $runBody -Name "requestId" }

$processResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/admin/tasks/campaigns/process/run?limit=100" -TimeoutSec 60
if ($processResp.StatusCode -eq 403 -or $processResp.StatusCode -eq 404) {
  Write-Host "[WARN] Campaigns process task not available for this token (403/404); skipping"
} else {
  $null = Assert-Ok -Resp $processResp -Action "Process campaigns"
}

$changed = $false
for ($i = 0; $i -lt 10; $i++) {
  Start-Sleep -Milliseconds 500
  $state = Get-CampaignState -CampaignId $campaignId
  if (-not $state) { continue }
  $queuedAt = Get-PropValue -Obj $state.body -Name "queued_at"
  $attempts = Get-PropValue -Obj $state.body -Name "attempts"
  $processing = Get-PropValue -Obj $state.body -Name "processing_status"
  if ($queuedAt -and $queuedAt -ne $beforeQueuedAt) { $changed = $true; break }
  if ($attempts -and $beforeAttempts -ne $null -and [int]$attempts -gt [int]$beforeAttempts) { $changed = $true; break }
  if ($processing -in @("done", "failed", "processing", "queued")) { $changed = $true; break }
}

if (-not $changed) {
  $stateInfo = $null
  if ($state) { $stateInfo = "processing_status=$processing queued_at=$queuedAt attempts=$attempts" }
  throw "Campaign run did not update status in time. $stateInfo"
}

$cleanupResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/admin/tasks/campaigns/cleanup/run?limit=100&done_days=1&failed_days=1" -TimeoutSec 60
if ($cleanupResp.StatusCode -eq 403 -or $cleanupResp.StatusCode -eq 404) {
  Write-Host "[WARN] Campaigns cleanup task not available for this token (403/404); deleting campaign"
  $deleteResp = Invoke-Api -Method "DELETE" -Url "$BaseUrl/api/v1/campaigns/$campaignId"
  if ($deleteResp.StatusCode -eq 409) {
    $detail = $null
    try {
      if ($deleteResp.Body -is [string]) {
        $detail = (ConvertFrom-Json $deleteResp.Body).detail
      } else {
        $detail = $deleteResp.Body.detail
      }
    } catch {
      $detail = $null
    }
    if ($detail -eq "campaigns_orm_mode_not_supported_for_this_endpoint") {
      Write-Host "[WARN] Campaign delete skipped: ORM guard active"
    } else {
      $null = Assert-Ok -Resp $deleteResp -Action "Delete campaign"
    }
  } elseif ($deleteResp.StatusCode -ne 204 -and $deleteResp.StatusCode -ne 200 -and $deleteResp.StatusCode -ne 404) {
    $null = Assert-Ok -Resp $deleteResp -Action "Delete campaign"
  }
} else {
  $null = Assert-Ok -Resp $cleanupResp -Action "Cleanup campaigns"
}

Write-Host "OK: campaigns e2e complete"
Write-Host ("  campaign_id: {0}" -f $campaignId)
if ($runRequestId) {
  Write-Host ("  run_id: {0}" -f $runRequestId)
}
Write-Host ("  cache: {0}" -f (Get-SmokeCachePath))
