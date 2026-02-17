param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL
)

$ErrorActionPreference = "Stop"

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

. (Join-Path $PSScriptRoot "_smoke-lib.ps1")

$authHeader = Ensure-SmartsellAuth -BaseUrl $BaseUrl

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

Write-Host "[INFO] Preorders: create"
$payload = @{
  currency = "KZT"
  customer_name = "Smoke Customer"
  customer_phone = "+77000000000"
  notes = "smoke preorder"
  items = @(
    @{
      sku = "SMOKE-001"
      name = "Smoke Item"
      qty = 1
      price = "100.00"
    }
  )
}
$createResp = Invoke-SmokeRequest -Method "POST" -Url "$BaseUrl/api/v1/preorders" -Body $payload
Assert-Status -Resp $createResp -Label "create preorder"
$preorderId = $createResp.Body.id
if (-not $preorderId) { throw "preorder id missing" }

Write-Host "[INFO] Preorders: list"
$listResp = Invoke-SmokeRequest -Method "GET" -Url "$BaseUrl/api/v1/preorders"
Assert-Status -Resp $listResp -Label "list preorders"

Write-Host "[INFO] Preorders: get"
$getResp = Invoke-SmokeRequest -Method "GET" -Url "$BaseUrl/api/v1/preorders/$preorderId"
Assert-Status -Resp $getResp -Label "get preorder"

Write-Host "[INFO] Preorders: confirm"
$confirmResp = Invoke-SmokeRequest -Method "POST" -Url "$BaseUrl/api/v1/preorders/$preorderId/confirm"
Assert-Status -Resp $confirmResp -Label "confirm preorder"

Write-Host "[INFO] Preorders: fulfill"
$fulfillResp = Invoke-SmokeRequest -Method "POST" -Url "$BaseUrl/api/v1/preorders/$preorderId/fulfill"
Assert-Status -Resp $fulfillResp -Label "fulfill preorder"
$fulfilledOrderId = $fulfillResp.Body.fulfilled_order_id
if (-not $fulfilledOrderId) { throw "fulfilled_order_id missing" }
Write-Host ("[INFO] Preorders: fulfilled_order_id={0}" -f $fulfilledOrderId)

Write-Host "[INFO] Orders: get"
$orderResp = Invoke-SmokeRequest -Method "GET" -Url "$BaseUrl/api/v1/orders/$fulfilledOrderId"
Assert-Status -Resp $orderResp -Label "get order"

Write-Host "[OK] Preorders E2E"
