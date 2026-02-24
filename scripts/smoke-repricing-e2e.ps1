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

function Debug-MissingId {
  param(
    [string]$Label,
    [object]$Obj
  )
  $typeName = $null
  try { $typeName = $Obj.GetType().FullName } catch { $typeName = "<unknown>" }
  $jsonText = $null
  try { $jsonText = $Obj | ConvertTo-Json -Depth 10 } catch { $jsonText = "<unserializable>" }
  Write-Host ("[DEBUG] Missing id for {0}. type={1} json={2}" -f $Label, $typeName, $jsonText)
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

$runId = ([guid]::NewGuid().ToString("N")).Substring(0, 8)
$productSku = "SMOKE-REPRICE-$runId"
$productSlug = "smoke-reprice-$runId"
$productName = "Smoke Reprice $runId"

$productPayload = @{
  name = $productName
  slug = $productSlug
  sku = $productSku
  price = 100
  stock_quantity = 0
}

$productResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/products" -Body $productPayload
if ($productResp.StatusCode -eq 409) {
  $listResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/products?search=$productSku&page=1&per_page=1"
  $listBody = Assert-Ok -Resp $listResp -Action "List products"
  $items = @(Get-RespItems -Body $listBody)
  if (-not $items -or $items.Count -eq 0) {
    throw "Product create failed and no existing product found"
  }
  $product = $items[0]
} else {
  $product = Assert-Ok -Resp $productResp -Action "Create product"
}

$productId = Get-Id -Obj $product
if (-not $productId) {
  Debug-MissingId -Label "product" -Obj $product
  throw "Missing product id"
}

$repricingConfigPayload = @{
  enabled = $true
  min = 50
  max = 150
  step = 1
  channel = "kaspi"
  friendly_ids = @()
  cooldown = 0
  hysteresis = 0
}

$cfgResp = Invoke-Api -Method "PUT" -Url "$BaseUrl/api/v1/products/$productId/repricing/config" -Body $repricingConfigPayload
$null = Assert-Ok -Resp $cfgResp -Action "Set repricing config"

$rulePayload = @{
  name = "Smoke Rule $runId"
  enabled = $true
  is_active = $true
  scope_type = "all"
  step = 1
  rounding_mode = "nearest"
}

$ruleResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/repricing/rules" -Body $rulePayload
$ruleId = $null
if ($ruleResp.StatusCode -eq 201) {
  $ruleBody = Assert-Ok -Resp $ruleResp -Action "Create repricing rule"
  $ruleId = Get-Id -Obj $ruleBody
} elseif ($ruleResp.StatusCode -eq 409 -or $ruleResp.StatusCode -eq 422) {
  $rulesListResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/repricing/rules?page=1&per_page=20"
  $rulesList = Assert-Ok -Resp $rulesListResp -Action "List repricing rules"
  $rules = @(Get-RespItems -Body $rulesList)
  if ($rules.Count -gt 0) {
    $ruleId = Get-Id -Obj $rules[0]
  }
} else {
  $null = Assert-Ok -Resp $ruleResp -Action "Create repricing rule"
}

if (-not $ruleId) {
  throw "Missing repricing rule id"
}

$runResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/repricing/run" -TimeoutSec 60
$runBody = Assert-Ok -Resp $runResp -Action "Trigger repricing run"
$runIdValue = Get-PropValue -Obj $runBody -Name "run_id"

$runIdResolved = $runIdValue
if (-not $runIdResolved) {
  for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 500
    $runsResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/repricing/runs?page=1&per_page=5" -TimeoutSec 30
    $runsBody = Assert-Ok -Resp $runsResp -Action "List repricing runs"
    $runs = @(Get-RespItems -Body $runsBody)
    if ($runs.Count -gt 0) {
      $runIdResolved = Get-Id -Obj $runs[0]
      if ($runIdResolved) { break }
    }
  }
}

if (-not $runIdResolved) {
  throw "Missing repricing run id"
}

$applyResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/repricing/runs/$runIdResolved/apply" -TimeoutSec 60
$null = Assert-Ok -Resp $applyResp -Action "Apply repricing run"

Write-Host "OK: repricing e2e complete"
Write-Host ("  product_id: {0}" -f $productId)
Write-Host ("  run_id: {0}" -f $runIdResolved)
Write-Host ("  cache: {0}" -f (Get-SmokeCachePath))
