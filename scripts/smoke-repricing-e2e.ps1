param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL
)

$ErrorActionPreference = "Stop"

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

. (Join-Path $PSScriptRoot "_smoke-lib.ps1")

$authHeader = Get-StoreAuthHeader -BaseUrl $BaseUrl

function Invoke-SmokeRequest {
  param(
    [string]$Method,
    [string]$Url,
    [object]$Body = $null
  )
  return Invoke-SmartsellApi -Method $Method -Url $Url -Headers $authHeader -Body $Body -TimeoutSec 20
}

function Assert-Status {
  param(
    [object]$Resp,
    [string]$Label,
    [int]$Min = 200,
    [int]$Max = 299
  )
  if (-not $Resp -or $Resp.StatusCode -lt $Min -or $Resp.StatusCode -gt $Max) {
    $status = if ($Resp) { $Resp.StatusCode } else { "(no response)" }
    $bodyText = if ($Resp) { $Resp.Body | ConvertTo-Json -Depth 10 } else { "" }
    throw "$Label failed: status=$status body=$bodyText"
  }
}

Write-Host "[INFO] Repricing: create rule"
$rulePayload = @{
  name = "smoke-rule-" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
  enabled = $true
  is_active = $true
  scope_type = "all"
  step = "5.00"
  rounding_mode = "nearest"
}
$ruleResp = Invoke-SmokeRequest -Method "POST" -Url "$BaseUrl/api/v1/repricing/rules" -Body $rulePayload
Assert-Status -Resp $ruleResp -Label "create repricing rule"
$ruleId = $ruleResp.Body.id
if (-not $ruleId) { throw "repricing rule id missing" }

Write-Host "[INFO] Repricing: run"
$runResp = Invoke-SmokeRequest -Method "POST" -Url "$BaseUrl/api/v1/repricing/run"
Assert-Status -Resp $runResp -Label "repricing run"
$runId = $runResp.Body.run_id
if (-not $runId) { throw "repricing run id missing" }

Write-Host "[INFO] Repricing: poll run status"
$maxTries = 10
$done = $false
for ($i = 0; $i -lt $maxTries; $i++) {
  $statusResp = Invoke-SmokeRequest -Method "GET" -Url "$BaseUrl/api/v1/repricing/runs/$runId"
  Assert-Status -Resp $statusResp -Label "repricing run status"
  $status = $statusResp.Body.status
  if ($status -eq "done" -or $status -eq "failed") {
    $done = $true
    if ($status -eq "failed") {
      throw "repricing run failed: $($statusResp.Body.last_error)"
    }
    break
  }
  Start-Sleep -Seconds 1
}

if (-not $done) { throw "repricing run did not finish in time" }

Write-Host "[INFO] Repricing: apply to Kaspi (dry_run)"
$applyResp = Invoke-SmokeRequest -Method "POST" -Url "$BaseUrl/api/v1/repricing/runs/$runId/apply?dry_run=true"
Assert-Status -Resp $applyResp -Label "repricing apply"
$applyItems = @($applyResp.Body.items)
if ($applyItems.Count -eq 0) { throw "repricing apply returned no items" }
$hasDryRun = $false
foreach ($item in $applyItems) {
  if ($item.status -eq "dry_run") { $hasDryRun = $true; break }
}
if (-not $hasDryRun) { throw "repricing apply did not return dry_run items" }

Write-Host "[OK] Repricing E2E"
