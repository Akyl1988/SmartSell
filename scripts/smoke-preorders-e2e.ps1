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
  if ($Body -and $Body.PSObject.Properties["items"]) {
    return @($Body.items)
  }
  return @()
}

function Get-Id {
  param([object]$Obj)
  if (-not $Obj) { return $null }
  $prop = $Obj.PSObject.Properties["id"]
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
$productSku = "SMOKE-PRE-$runId"
$productSlug = "smoke-pre-$runId"
$productName = "Smoke Preorder $runId"

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
    $items = Get-RespItems -Body $listBody
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

$whListResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/warehouses?page=1&per_page=100"
$whList = Assert-Ok -Resp $whListResp -Action "List warehouses"
$warehouses = Get-RespItems -Body $whList
$mainWarehouse = $warehouses | Where-Object { $_.is_main -eq $true -and $_.is_active -eq $true } | Select-Object -First 1
if (-not $mainWarehouse) {
  $whCreate = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/warehouses" -Body @{ name = "Main"; is_main = $true }
  $mainWarehouse = Assert-Ok -Resp $whCreate -Action "Create main warehouse"
}

$warehouseId = Get-Id -Obj $mainWarehouse
if (-not $warehouseId) {
  $whListResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/warehouses?page=1&per_page=100"
  $whList = Assert-Ok -Resp $whListResp -Action "Re-list warehouses"
  $warehouses = Get-RespItems -Body $whList
  $mainWarehouse = $warehouses | Where-Object { $_.is_main -eq $true -and $_.is_active -eq $true } | Select-Object -First 1
  $warehouseId = Get-Id -Obj $mainWarehouse
}

if (-not $warehouseId) {
  Debug-MissingId -Label "warehouse" -Obj $mainWarehouse
  throw "Missing warehouse id (restart API after schema update if responses omit id)"
}

$stockListResp = Invoke-Api -Method "GET" -Url "$BaseUrl/api/v1/inventory/stocks?warehouse_id=$warehouseId&product_id=$productId&page=1&per_page=1"
$stockList = Assert-Ok -Resp $stockListResp -Action "List stocks"
$stockItem = $null
$stockItems = Get-RespItems -Body $stockList
if ($stockItems.Count -gt 0) { $stockItem = $stockItems[0] }

$qty = 0
$reserved = 0
if ($stockItem) {
  $qty = [int]($stockItem.quantity ?? 0)
  $reserved = [int]($stockItem.reserved_quantity ?? 0)
}

if ($reserved -gt 0) {
  $releasePayload = @{
    product_id = $productId
    qty = $reserved
    warehouse_id = $warehouseId
    reference_type = "smoke-preorders"
    reference_id = [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
  }
  $releaseResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/inventory/reservations/release" -Body $releasePayload
  Assert-Ok -Resp $releaseResp -Action "Release reserved stock"
  $reserved = 0
}

if ($qty -lt 3) {
  $delta = 3 - $qty
  $movePayload = @{
    warehouse_id = $warehouseId
    product_id = $productId
    qty_delta = $delta
    reason = "smoke-ensure-stock"
    reference = "smoke-preorders"
  }
  $moveResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/inventory/movements" -Body $movePayload
  Assert-Ok -Resp $moveResp -Action "Seed stock movement"
}

$preorderPayload = @{
  currency = "KZT"
  customer_name = "Smoke Customer"
  items = @(
    @{
      product_id = $productId
      sku = $productSku
      name = $productName
      qty = 2
      price = "100.00"
    }
  )
}

$preorderResp = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/preorders" -Body $preorderPayload
$preorder = Assert-Ok -Resp $preorderResp -Action "Create preorder"
$preorderId = Get-Id -Obj $preorder
if (-not $preorderId) {
  Debug-MissingId -Label "preorder" -Obj $preorder
  throw "Missing preorder id"
}

$confirm1 = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/preorders/$preorderId/confirm"
Assert-Ok -Resp $confirm1 -Action "Confirm preorder"

$cancel = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/preorders/$preorderId/cancel"
Assert-Ok -Resp $cancel -Action "Cancel preorder"

$confirm2 = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/preorders/$preorderId/confirm"
Assert-Ok -Resp $confirm2 -Action "Confirm preorder again"

$fulfill = Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/preorders/$preorderId/fulfill"
Assert-Ok -Resp $fulfill -Action "Fulfill preorder"

Write-Host "OK: preorder e2e complete"
Write-Host ("  product_id: {0}" -f $productId)
Write-Host ("  warehouse_id: {0}" -f $warehouseId)
Write-Host ("  preorder_id: {0}" -f $preorderId)
Write-Host ("  cache: {0}" -f (Get-SmokeCachePath))
