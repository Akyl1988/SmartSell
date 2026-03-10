param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL,
  [switch]$DryRun
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

function Get-TokenFromHeaders {
  param([hashtable]$Headers)
  if (-not $Headers) { return $null }
  if (-not $Headers.Authorization) { return $null }
  return ([string]$Headers.Authorization).Replace("Bearer ", "").Trim()
}

function Get-SubState {
  param([object]$Sub)
  if (-not $Sub) {
    return [PSCustomObject]@{ id = $null; status = "none"; plan = ""; period_end = ""; grace_until = "" }
  }

  $id = if ($Sub.PSObject.Properties["id"]) { $Sub.id } else { $null }
  $status = if ($Sub.PSObject.Properties["status"]) { [string]$Sub.status } else { "" }
  $periodEnd = ""
  $graceUntil = ""
  $plan = ""
  if ($Sub.PSObject.Properties["plan"]) { $plan = [string]$Sub.plan }
  if ($Sub.PSObject.Properties["period_end"]) { $periodEnd = [string]$Sub.period_end }
  if ($Sub.PSObject.Properties["periodEnd"]) { $periodEnd = [string]$Sub.periodEnd }
  if ($Sub.PSObject.Properties["grace_until"]) { $graceUntil = [string]$Sub.grace_until }
  if ($Sub.PSObject.Properties["graceUntil"]) { $graceUntil = [string]$Sub.graceUntil }

  return [PSCustomObject]@{
    id = $id
    status = $status
    plan = $plan
    period_end = $periodEnd
    grace_until = $graceUntil
  }
}

function Test-PreordersFeatureEligibility {
  param(
    [string]$BaseUrl,
    [string]$AccessToken
  )

  $resp = Invoke-SmartsellApi -Method "POST" -Url "$BaseUrl/api/v1/preorders" -Body @{} -AccessToken $AccessToken -TimeoutSec 20
  if ($resp.StatusCode -eq 403) {
    $detail = $null
    if ($resp.Body -and $resp.Body.PSObject.Properties["detail"]) { $detail = $resp.Body.detail }
    if ($detail -and $detail.PSObject.Properties["code"] -and [string]$detail.code -eq "FEATURE_NOT_AVAILABLE") {
      if ($detail.PSObject.Properties["feature"] -and [string]$detail.feature -eq "preorders") {
        return $false
      }
    }
  }

  return $true
}

Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null
$authHeaders = Get-SmokeAuthHeader -BaseUrl $BaseUrl
$accessToken = Get-TokenFromHeaders -Headers $authHeaders
if (-not $accessToken) { throw "Failed to resolve access token from smoke auth header" }

$meResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/auth/me" -AccessToken $accessToken -TimeoutSec 20
if ($meResp.StatusCode -lt 200 -or $meResp.StatusCode -ge 300) {
  $bodyText = $null
  try { $bodyText = $meResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $meResp.Body }
  throw "auth/me failed: status=$($meResp.StatusCode) body=$bodyText"
}

$companyId = Resolve-ProfileValue -Profile $meResp.Body -Name "company_id"
if (-not $companyId) { throw "tenant prepare failed: company_id not found in auth profile" }

$currentResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/subscriptions/current" -AccessToken $accessToken -TimeoutSec 20
if ($currentResp.StatusCode -lt 200 -or $currentResp.StatusCode -ge 300) {
  $bodyText = $null
  try { $bodyText = $currentResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $currentResp.Body }
  throw "subscriptions/current failed: status=$($currentResp.StatusCode) body=$bodyText"
}

$previousState = Get-SubState -Sub $currentResp.Body
$actionTaken = "none"

if (-not $DryRun) {
  if ($previousState.status -eq "none") {
    $createPayload = @{
      plan = "Start"
      billing_cycle = "monthly"
      price = "0"
      currency = "KZT"
      trial_days = 0
    }
    $createResp = Invoke-SmartsellApi -Method "POST" -Url "$BaseUrl/api/v1/subscriptions" -Body $createPayload -AccessToken $accessToken -TimeoutSec 20
    if ($createResp.StatusCode -eq 201) {
      $actionTaken = "create_subscription"
    } elseif ($createResp.StatusCode -eq 409) {
      $actionTaken = "none_conflict_existing_active"
    } else {
      $bodyText = $null
      try { $bodyText = $createResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $createResp.Body }
      throw "create subscription failed: status=$($createResp.StatusCode) body=$bodyText"
    }
  } elseif ($previousState.status -ne "active") {
    if (-not $previousState.id) {
      throw "subscription prepare failed: current subscription has no id for renew"
    }
    $renewResp = Invoke-SmartsellApi -Method "POST" -Url "$BaseUrl/api/v1/subscriptions/$($previousState.id)/renew" -AccessToken $accessToken -TimeoutSec 20
    if ($renewResp.StatusCode -lt 200 -or $renewResp.StatusCode -ge 300) {
      $bodyText = $null
      try { $bodyText = $renewResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $renewResp.Body }
      throw "renew subscription failed: status=$($renewResp.StatusCode) body=$bodyText"
    }
    $actionTaken = "renew_subscription"
  }
}

$resultResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/subscriptions/current" -AccessToken $accessToken -TimeoutSec 20
if ($resultResp.StatusCode -lt 200 -or $resultResp.StatusCode -ge 300) {
  $bodyText = $null
  try { $bodyText = $resultResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $resultResp.Body }
  throw "subscriptions/current (post-action) failed: status=$($resultResp.StatusCode) body=$bodyText"
}

$resultingState = Get-SubState -Sub $resultResp.Body
$smokeAllowed = $false
$preflightError = $null

try {
  Test-SmokeTenantProductCreatePreflight -BaseUrl $BaseUrl -AccessToken $accessToken -TimeoutSec 20 | Out-Null
  $smokeAllowed = $true
} catch {
  $smokeAllowed = $false
  $preflightError = $_.Exception.Message
}

if ((-not $smokeAllowed) -and (-not $DryRun)) {
  if ($resultingState.id) {
    $cancelResp = Invoke-SmartsellApi -Method "POST" -Url "$BaseUrl/api/v1/subscriptions/$($resultingState.id)/cancel" -AccessToken $accessToken -TimeoutSec 20
    if ($cancelResp.StatusCode -ge 200 -and $cancelResp.StatusCode -lt 300) {
      if ($actionTaken -eq "none") {
        $actionTaken = "cancel_and_create_subscription"
      } else {
        $actionTaken = "$actionTaken;cancel_and_create_subscription"
      }
    } else {
      $bodyText = $null
      try { $bodyText = $cancelResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $cancelResp.Body }
      throw "cancel subscription failed: status=$($cancelResp.StatusCode) body=$bodyText"
    }
  }

  $createPayload2 = @{
    plan = "Start"
    billing_cycle = "monthly"
    price = "0"
    currency = "KZT"
    trial_days = 0
  }
  $createResp2 = Invoke-SmartsellApi -Method "POST" -Url "$BaseUrl/api/v1/subscriptions" -Body $createPayload2 -AccessToken $accessToken -TimeoutSec 20
  if ($createResp2.StatusCode -ne 201) {
    $bodyText = $null
    try { $bodyText = $createResp2.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $createResp2.Body }
    throw "create replacement subscription failed: status=$($createResp2.StatusCode) body=$bodyText"
  }

  $resultResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/subscriptions/current" -AccessToken $accessToken -TimeoutSec 20
  if ($resultResp.StatusCode -lt 200 -or $resultResp.StatusCode -ge 300) {
    $bodyText = $null
    try { $bodyText = $resultResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $resultResp.Body }
    throw "subscriptions/current (after replacement) failed: status=$($resultResp.StatusCode) body=$bodyText"
  }
  $resultingState = Get-SubState -Sub $resultResp.Body

  try {
    Test-SmokeTenantProductCreatePreflight -BaseUrl $BaseUrl -AccessToken $accessToken -TimeoutSec 20 | Out-Null
    $smokeAllowed = $true
    $preflightError = $null
  } catch {
    $smokeAllowed = $false
    $preflightError = $_.Exception.Message
  }
}

Write-Host ("TENANT_COMPANY_ID={0}" -f $companyId)
Write-Host ("PREVIOUS_SUBSCRIPTION_STATE=status:{0};plan:{1};id:{2};period_end:{3};grace_until:{4}" -f $previousState.status, $previousState.plan, $previousState.id, $previousState.period_end, $previousState.grace_until)
if ($smokeAllowed -and (-not (Test-PreordersFeatureEligibility -BaseUrl $BaseUrl -AccessToken $accessToken)) -and (-not $DryRun)) {
  if (-not $resultingState.id) {
    throw "feature preflight failed: no current subscription id for plan update"
  }

  $patchPayload = @{ plan = "pro" }
  $patchResp = Invoke-SmartsellApi -Method "PATCH" -Url "$BaseUrl/api/v1/subscriptions/$($resultingState.id)" -Body $patchPayload -AccessToken $accessToken -TimeoutSec 20
  if ($patchResp.StatusCode -lt 200 -or $patchResp.StatusCode -ge 300) {
    $bodyText = $null
    try { $bodyText = $patchResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $patchResp.Body }
    throw "subscription plan update failed: status=$($patchResp.StatusCode) body=$bodyText"
  }

  if ($actionTaken -eq "none") {
    $actionTaken = "update_plan_to_pro"
  } else {
    $actionTaken = "$actionTaken;update_plan_to_pro"
  }

  $resultResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/subscriptions/current" -AccessToken $accessToken -TimeoutSec 20
  if ($resultResp.StatusCode -lt 200 -or $resultResp.StatusCode -ge 300) {
    $bodyText = $null
    try { $bodyText = $resultResp.Body | ConvertTo-Json -Depth 10 } catch { $bodyText = $resultResp.Body }
    throw "subscriptions/current (after plan update) failed: status=$($resultResp.StatusCode) body=$bodyText"
  }
  $resultingState = Get-SubState -Sub $resultResp.Body

  try {
    Test-SmokeTenantProductCreatePreflight -BaseUrl $BaseUrl -AccessToken $accessToken -TimeoutSec 20 | Out-Null
    $smokeAllowed = Test-PreordersFeatureEligibility -BaseUrl $BaseUrl -AccessToken $accessToken
    if (-not $smokeAllowed) {
      $preflightError = "FEATURE_NOT_AVAILABLE for preorders after plan update"
    } else {
      $preflightError = $null
    }
  } catch {
    $smokeAllowed = $false
    $preflightError = $_.Exception.Message
  }
}

Write-Host ("ACTION_TAKEN={0}" -f $actionTaken)
Write-Host ("RESULTING_SUBSCRIPTION_STATE=status:{0};plan:{1};id:{2};period_end:{3};grace_until:{4}" -f $resultingState.status, $resultingState.plan, $resultingState.id, $resultingState.period_end, $resultingState.grace_until)
Write-Host ("SMOKE_ALLOWED={0}" -f $smokeAllowed)
if ($preflightError) {
  Write-Host ("SMOKE_PREFLIGHT_ERROR={0}" -f $preflightError)
}

if (-not $smokeAllowed) {
  throw "Tenant preparation completed but smoke preflight is still blocked"
}
