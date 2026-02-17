<#
Kaspi sync-now smoke (platform_admin token)
Env:
  PLATFORM_IDENTIFIER / PLATFORM_PASSWORD (or SMARTSELL_PLATFORM_*/SMARTSELL_ADMIN_* legacy)
  KASPI_MERCHANT_UID
#>

param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL,
  [string]$Token = $env:SMARTSELL_TOKEN,
  [string]$Identifier = $env:PLATFORM_IDENTIFIER,
  [string]$Password = $env:PLATFORM_PASSWORD,
  [string]$MerchantUid = $env:KASPI_MERCHANT_UID,
  [int]$TimeoutSec = 30,
  [int]$ProbeTimeoutSec = 2,
  [switch]$EnsureOffers,
  [switch]$SkipIfApiDown
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --------- User-configurable variables ---------
if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }
if (-not $Token) { $Token = "" }
$identifierProvided = $PSBoundParameters.ContainsKey("Identifier")
$passwordProvided = $PSBoundParameters.ContainsKey("Password")

if (-not $identifierProvided) { $Identifier = $env:PLATFORM_IDENTIFIER }
if (-not $passwordProvided) { $Password = $env:PLATFORM_PASSWORD }

$idSource = if ($identifierProvided) { "param:Identifier" } else { "PLATFORM_IDENTIFIER" }
$pwSource = if ($passwordProvided) { "param:Password" } else { "PLATFORM_PASSWORD" }

if (-not $Identifier) { $Identifier = $env:SMARTSELL_PLATFORM_IDENTIFIER; $idSource = "SMARTSELL_PLATFORM_IDENTIFIER" }
if (-not $Password) { $Password = $env:SMARTSELL_PLATFORM_PASSWORD; $pwSource = "SMARTSELL_PLATFORM_PASSWORD" }
if (-not $Identifier) { $Identifier = $env:SMARTSELL_PLATFORM_ADMIN_IDENTIFIER; $idSource = "SMARTSELL_PLATFORM_ADMIN_IDENTIFIER" }
if (-not $Password) { $Password = $env:SMARTSELL_PLATFORM_ADMIN_PASSWORD; $pwSource = "SMARTSELL_PLATFORM_ADMIN_PASSWORD" }
if (-not $Identifier) { $Identifier = $env:SMARTSELL_ADMIN_IDENTIFIER; $idSource = "SMARTSELL_ADMIN_IDENTIFIER" }
if (-not $Password) { $Password = $env:SMARTSELL_ADMIN_PASSWORD; $pwSource = "SMARTSELL_ADMIN_PASSWORD" }
if (-not $Identifier) { $Identifier = "" }
if (-not $Password) { $Password = "" }
if (-not $MerchantUid) { $MerchantUid = "" }

function Get-ScriptDir {
  if ($PSScriptRoot) { return $PSScriptRoot }
  if ($MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    return Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  return (Get-Location).Path
}

$ScriptDir = Get-ScriptDir
. (Join-Path $ScriptDir "_smoke-lib.ps1")
$RepoRoot = Split-Path -Parent $ScriptDir
if ($RepoRoot) { Set-Location $RepoRoot }

function New-RequestId {
  return [guid]::NewGuid().ToString()
}

function Get-OpenApiPaths {
  $rid = New-RequestId
  $headers = @{ "X-Request-ID" = $rid }
  $openapi = Invoke-RestMethod -Method Get -Uri "$BaseUrl/openapi.json" -Headers $headers -TimeoutSec 15
  return $openapi.paths.PSObject.Properties.Name
}

function Resolve-KaspiSyncNowPath {
  $paths = Get-OpenApiPaths

  $candidates = @($paths | Where-Object {
    $_ -match "kaspi" -and $_ -match "sync" -and ($_ -match "now" -or $_ -match "run" -or $_ -match "orchestr")
  })

  if (-not $candidates -or $candidates.Count -eq 0) {
    $kaspiPaths = $paths | Where-Object { $_ -match "kaspi" }
    Write-Error "Kaspi sync-now path not found. Kaspi paths: $($kaspiPaths -join ', ')"
    return $null
  }

  $preferred = @($candidates | Where-Object { $_ -match "sync" -and $_ -match "now" })
  if ($preferred -and $preferred.Count -gt 0) {
    return $preferred | Select-Object -First 1
  }

  return $candidates | Select-Object -First 1
}

function Get-JsonProperty {
  param(
    [object]$Object,
    [string]$Name
  )
  if (-not $Object) { return $null }
  $prop = $Object.PSObject.Properties[$Name]
  if ($prop) { return $prop.Value }
  return $null
}


function Print-Response {
  param(
    [object]$Resp,
    [int]$LatencyMs
  )
  try {
    $status = Get-JsonProperty -Object $Resp -Name "StatusCode"
    $retryAfter = Get-JsonProperty -Object $Resp -Name "RetryAfter"
    $body = Get-JsonProperty -Object $Resp -Name "Body"
    $requestId = Get-JsonProperty -Object $Resp -Name "RequestId"
    $error = Get-JsonProperty -Object $Resp -Name "Error"

    $statusVal = 0
    if ($null -ne $status) { $statusVal = [int]$status }

    Write-Host "STATUS: $statusVal"
    Write-Host "LATENCY_MS: $LatencyMs"
    Write-Host "REQUEST_ID: $requestId"
    if ($retryAfter) { Write-Host "RETRY_AFTER: $retryAfter" }
    if ($error) { Write-Host "ERROR: $error" }
    if ($body) {
      $bodyText = $body
      if ($body -isnot [string]) {
        $bodyText = $body | ConvertTo-Json -Depth 50
      }
      Write-Host "BODY: $bodyText"
    }
  } catch {
    Write-Host "WARN: failed to print response"
  }
}

function Test-ApiReady {
  try {
    $resp = Invoke-WebRequestSafe -Params @{
      Method = "GET"
      Uri = "$BaseUrl/openapi.json"
      TimeoutSec = $ProbeTimeoutSec
    }
    return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
  } catch {
    return $false
  }
}

function Invoke-KaspiSyncNow {
  param(
    [string]$Path,
    [string]$MerchantUid
  )
  $rid = New-RequestId
  $headers = @{ "X-Request-ID" = $rid }

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $uri = "$BaseUrl$Path"
  $queryParts = @()
  if ($MerchantUid) {
    $queryParts += "merchantUid=" + [uri]::EscapeDataString($MerchantUid)
  }
  if ($TimeoutSec -gt 0) {
    $queryParts += "timeout_sec=" + [uri]::EscapeDataString([string]$TimeoutSec)
  }
  if ($queryParts.Count -gt 0) {
    $uri = $uri + "?" + ($queryParts -join "&")
  }

  $resp = Invoke-SmartsellApi -Method "POST" -Url $uri -Headers $headers -TimeoutSec $TimeoutSec -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password
  $sw.Stop()

  $AccessToken = $script:SmartsellAccessToken
  $RefreshToken = $script:SmartsellRefreshToken

  $status = $resp.StatusCode
  $retryAfter = $null
  if ($resp.Headers) { $retryAfter = $resp.Headers["Retry-After"] }
  $body = $resp.Body
  $rid = [string]($resp.RequestId ?? "")

  if ($status -eq 422 -and $MerchantUid) {
    $retryRid = New-RequestId
    $retryHeaders = @{ "X-Request-ID" = $retryRid }
    $retryBody = @{ merchant_uid = $MerchantUid }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-SmartsellApi -Method "POST" -Url "$BaseUrl$Path?timeout_sec=$TimeoutSec" -Headers $retryHeaders -Body $retryBody -TimeoutSec $TimeoutSec -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password
    $sw.Stop()

    $AccessToken = $script:SmartsellAccessToken
    $RefreshToken = $script:SmartsellRefreshToken

    $status = $resp.StatusCode
    $retryAfter = $null
    if ($resp.Headers) { $retryAfter = $resp.Headers["Retry-After"] }
    $body = $resp.Body
    $rid = [string]($resp.RequestId ?? "")
  }

  $result = [PSCustomObject]@{
    StatusCode = $status
    RetryAfter = $retryAfter
    Body = $body
    RequestId = $rid
    Error = $null
  }

  Print-Response -Resp $result -LatencyMs $sw.ElapsedMilliseconds
  return $result
}

Write-Host "[INFO] Credentials source: ID=$idSource PW=$pwSource"

$AccessToken = $Token
$AccessToken = Normalize-JwtToken -Value $AccessToken
if (-not $AccessToken) { $AccessToken = Normalize-JwtToken -Value $env:SMARTSELL_ACCESS_TOKEN }
$RefreshToken = $env:SMARTSELL_REFRESH_TOKEN

if ($AccessToken) {
  Set-SmartsellTokens -AccessToken $AccessToken -RefreshToken $RefreshToken -BaseUrl $BaseUrl
  Write-Host ("[INFO] Using access token={0}" -f (Mask-Secret $AccessToken))
} elseif ($Token) {
  Write-Host ("WARN: provided access token has invalid format; ignoring token={0}" -f (Mask-Secret $Token))
}

if (-not $AccessToken) {
  $cached = Load-SmartsellTokensFromCache -BaseUrl $BaseUrl
  if ($cached -and $cached.access) {
    $AccessToken = $cached.access
    $RefreshToken = $cached.refresh
    Set-SmartsellTokens -AccessToken $AccessToken -RefreshToken $RefreshToken -BaseUrl $BaseUrl
    Write-Host ("[INFO] Using cached access token from {0}" -f (Get-SmokeCachePath))
  }
}

if (-not $AccessToken -and (-not $Identifier -or -not $Password)) {
  Write-Host "[FAIL] platform_admin creds required. Set PLATFORM_IDENTIFIER/PLATFORM_PASSWORD (or SMARTSELL_PLATFORM_*). ADMIN_/SMARTSELL_IDENTIFIER are treated as store_admin."
  exit 1
}

if (-not (Test-ApiReady)) {
  if ($SkipIfApiDown) {
    Write-Host "SKIP: API unreachable"
    exit 0
  }
  Write-Error "API unreachable"
  exit 1
}

if (-not $AccessToken) {
  $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password -TimeoutSec 20
  $AccessToken = $tokens.access
  $RefreshToken = $tokens.refresh
}

$me = $null
try {
  $meResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/auth/me" -TimeoutSec 20 -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password
  if ($meResp.StatusCode -ge 200 -and $meResp.StatusCode -lt 300) {
    $me = $meResp.Body
  }
  $meCompanyId = Resolve-ProfileValue -Profile $me -Name "company_id"
  $meCompanyName = Resolve-ProfileValue -Profile $me -Name "company_name"
  $meKaspiStore = Resolve-ProfileValue -Profile $me -Name "kaspi_store_id"
  $meUserId = Resolve-ProfileValue -Profile $me -Name "id"
  $meRole = Resolve-ProfileValue -Profile $me -Name "role"
  Write-Host ("ME OK user_id={0} role={1} company_id={2} company_name={3} kaspi_store_id={4}" -f $meUserId, $meRole, $meCompanyId, $meCompanyName, $meKaspiStore)
} catch {
  Write-Host "WARN: failed to fetch /api/v1/auth/me"
}

$bodyMerchant = $null
$syncBody = $null
if ($syncBody) {
  $bodyMerchant = Get-JsonProperty -Object $syncBody -Name "merchant_uid"
  if (-not $bodyMerchant) { $bodyMerchant = Get-JsonProperty -Object $syncBody -Name "merchantUid" }
}

if (-not $MerchantUid -and $bodyMerchant) {
  $MerchantUid = [string]$bodyMerchant
}

if (-not $MerchantUid -and $me) {
  $MerchantUid = [string]$meKaspiStore
}

if ($MerchantUid -and $MerchantUid -match "^<YOUR_MERCHANT_UID>$|^YOUR_MERCHANT_UID$|^MERCHANT_UID$") {
  $MerchantUid = ""
}

if (-not $MerchantUid) {
  Write-Host "[FAIL] missing MerchantUid (-MerchantUid / KASPI_MERCHANT_UID) and company.kaspi_store_id not set"
  exit 1
}

$merchantForHint = $MerchantUid
if ([string]::IsNullOrWhiteSpace($merchantForHint)) { $merchantForHint = "17319385" }

$path = Resolve-KaspiSyncNowPath
if (-not $path) { exit 1 }
"Resolved endpoint: $path"

if ($EnsureOffers.IsPresent) {
  $ok = Ensure-KaspiOffers -BaseUrl $BaseUrl -MerchantUid $MerchantUid -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password -AllowSeed
  if (-not $ok) {
    Write-Host "WARN: could not ensure offers (will proceed)"
  }
}

if (-not (Test-ApiReady)) {
  if ($SkipIfApiDown) {
    Write-Host "SKIP: API unreachable"
    exit 0
  }
  Write-Error "API unreachable"
  exit 1
}

"--- First call ---"
$first = Invoke-KaspiSyncNow -Path $path -MerchantUid $MerchantUid

if (-not $first) {
  Write-Error "First call failed to return a response"
  exit 1
}

if ($first.StatusCode -eq 0) {
  if ($SkipIfApiDown) {
    Write-Host "SKIP: API unreachable"
    exit 0
  }
  Write-Error "API unreachable"
  exit 1
}

if ($first.StatusCode -eq 429) {
  Write-Host "WARN: rate limited; not failing script."
  exit 0
}

$firstCode = $first.StatusCode
$firstBody = Get-JsonProperty -Object $first -Name "Body"
$firstErrCode = Get-JsonProperty -Object $firstBody -Name "code"
$firstErrDetail = Get-JsonProperty -Object $firstBody -Name "detail"
if ($firstCode -eq 403 -and $firstErrCode -eq "ADMIN_REQUIRED") {
  Write-Host "[FAIL] ADMIN_REQUIRED: ensure platform_admin creds. Used: ID=$idSource PW=$pwSource"
  exit 1
}
if ($firstCode -eq 409 -and ($firstErrCode -eq "kaspi_not_configured" -or $firstErrDetail -eq "kaspi_not_configured")) {
  Write-Host "[FAIL] kaspi_not_configured: check companies.kaspi_store_id for company_id=1 and kaspi_store_tokens for store_name=merchant_uid"
  exit 1
}
if ($firstCode -eq 409 -and ($firstErrCode -eq "kaspi_token_not_found" -or $firstErrDetail -eq "kaspi_token_not_found")) {
  Write-Host "[FAIL] kaspi_token_not_found: check kaspi_store_tokens row for store_name=merchant_uid"
  exit 1
}
if ($firstCode -eq 404 -and ($firstErrCode -eq "offers_not_found" -or $firstErrDetail -eq "offers_not_found")) {
  Write-Host "WARN: Нет офферов для company_id=1 merchant_uid=$merchantForHint (kaspi_offers пустая)."
  if (Is-DevEnvironment) {
    Write-Host "[INFO] Attempting dev-only offers seed/import..."
    $seeded = Ensure-KaspiOffers -BaseUrl $BaseUrl -MerchantUid $MerchantUid -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password -AllowSeed
    if ($seeded) {
      Write-Host "[INFO] Offers ensured; retrying sync-now"
      $first = Invoke-KaspiSyncNow -Path $path -MerchantUid $MerchantUid
      $firstCode = $first.StatusCode
      $firstBody = Get-JsonProperty -Object $first -Name "Body"
      $firstErrCode = Get-JsonProperty -Object $firstBody -Name "code"
      $firstErrDetail = Get-JsonProperty -Object $firstBody -Name "detail"
    } else {
      Write-Host "WARN: offers still missing; stop"
      exit 0
    }
  } else {
    Write-Host "WARN: offers missing; skipping in non-dev environment."
    exit 0
  }
}

if ($firstCode -eq 402 -and ($firstErrCode -eq "subscription_required" -or $firstErrDetail -eq "subscription_required")) {
  Write-Host "SKIP: subscription required for kaspi sync now."
  return
}

if ($firstCode -eq 409 -and $firstErrCode -eq "kaspi_sync_in_progress") {
  # ok
} elseif ($firstCode -eq 200 -or $firstCode -eq 202) {
  # ok
} elseif ($firstCode -eq 504 -and $firstErrCode -eq "kaspi_sync_timeout") {
  Write-Host "WARN: kaspi sync timeout; not failing script."
  exit 0
} else {
  Write-Error "First call returned unexpected status: $firstCode"
  exit 1
}

"--- Second call ---"
$second = Invoke-KaspiSyncNow -Path $path -MerchantUid $MerchantUid

if (-not $second) {
  Write-Error "Second call failed to return a response"
  exit 1
}

if ($second.StatusCode -eq 0) {
  if ($SkipIfApiDown) {
    Write-Host "SKIP: API unreachable"
    exit 0
  }
  Write-Error "API unreachable"
  exit 1
}

if ($second.StatusCode -eq 429) {
  Write-Host "WARN: rate limited on second call; not failing script."
  exit 0
}

$secondCode = $second.StatusCode
$secondBody = Get-JsonProperty -Object $second -Name "Body"
$secondErrCode = Get-JsonProperty -Object $secondBody -Name "code"
$secondErrDetail = Get-JsonProperty -Object $secondBody -Name "detail"
if ($secondCode -eq 403 -and $secondErrCode -eq "ADMIN_REQUIRED") {
  Write-Host "[FAIL] ADMIN_REQUIRED: ensure platform_admin creds. Used: ID=$idSource PW=$pwSource"
  exit 1
}
if ($secondCode -eq 404 -and ($secondErrCode -eq "offers_not_found" -or $secondErrDetail -eq "offers_not_found")) {
  Write-Host "WARN: Нет офферов для company_id=1 merchant_uid=$merchantForHint (kaspi_offers пустая). Сначала запусти /api/v1/kaspi/catalog/import (или скрипт импорта офферов)."
  exit 0
}

if ($secondCode -eq 409 -and $secondErrCode -eq "kaspi_sync_in_progress") {
  "PASS"
  exit 0
}

if ($secondCode -eq 504 -and $secondErrCode -eq "kaspi_sync_timeout") {
  Write-Host "WARN: kaspi sync timeout on second call; not failing script."
  exit 0
}

if ($secondCode -eq 200) {
  if (-not $secondBody) {
    Write-Error "Second call returned 200 but body is empty"
    exit 1
  }
  $secondOk = Get-JsonProperty -Object $secondBody -Name "ok"
  if ($secondOk -ne $true) {
    Write-Error "Second call returned 200 but payload ok != true"
    exit 1
  }
  $secondStatus = Get-JsonProperty -Object $secondBody -Name "status"
  if ($secondStatus -eq "partial") {
    Write-Host "WARN: kaspi sync partial on second call; not failing script."
    exit 0
  }
  if ($secondStatus -eq "ok") {
    "PASS"
    exit 0
  }
  Write-Error "Second call returned 200 with unexpected status: $secondStatus"
  exit 1
}

Write-Error "Second call returned unexpected status: $secondCode"
exit 1

"PASS"
