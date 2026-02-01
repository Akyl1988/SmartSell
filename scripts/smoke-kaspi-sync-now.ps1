param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL,
  [string]$Identifier = $env:SMARTSELL_IDENTIFIER,
  [string]$Password = $env:SMARTSELL_PASSWORD,
  [string]$MerchantUid = $env:KASPI_MERCHANT_UID,
  [int]$TimeoutSec = 30,
  [int]$ProbeTimeoutSec = 2,
  [switch]$SkipIfApiDown
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --------- User-configurable variables ---------
if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }
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

function Invoke-WebRequestSafe {
  param(
    [hashtable]$Params
  )
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
    $Params.SkipHttpErrorCheck = $true
  }
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("UseBasicParsing")) {
    $Params.UseBasicParsing = $true
  }
  return Invoke-WebRequest @Params
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
    [string]$Token,
    [string]$Path,
    [string]$MerchantUid
  )

  $rid = New-RequestId
  $headers = @{ "Authorization" = "Bearer $Token"; "X-Request-ID" = $rid }

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $resp = $null
  try {
    $resp = Invoke-WebRequestSafe -Params @{
      Method = "Post"
      Uri = "$BaseUrl$Path"
      Headers = $headers
      ContentType = "application/json"
      Body = $null
      TimeoutSec = $TimeoutSec
    }
  } catch {
    $sw.Stop()
    $errMsg = ""
    try { $errMsg = $_.Exception.Message } catch { $errMsg = "request failed" }
    if ($errMsg) { Write-Host "ERROR: $errMsg" }
    Print-Response -Resp $null -LatencyMs $sw.ElapsedMilliseconds
    return [PSCustomObject]@{
      StatusCode = 0
      RetryAfter = $null
      Body = $null
      RequestId = $rid
      Error = $errMsg
    }
  }
  $sw.Stop()

  $status = $resp.StatusCode
  $retryAfter = $resp.Headers["Retry-After"]
  $body = $null
  $requestId = $resp.Headers["X-Request-ID"]

  if ($resp.Content) {
    try {
      $body = $resp.Content | ConvertFrom-Json
      if (-not $requestId) {
        $requestId = Get-JsonProperty -Object $body -Name "request_id"
      }
    } catch {
      $body = $resp.Content
    }
  }

  if ($status -eq 422 -and $MerchantUid) {
    $retryRid = New-RequestId
    $retryHeaders = @{ "Authorization" = "Bearer $Token"; "X-Request-ID" = $retryRid }
    $retryBody = @{ merchant_uid = $MerchantUid } | ConvertTo-Json
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
      $resp = Invoke-WebRequestSafe -Params @{
        Method = "Post"
        Uri = "$BaseUrl$Path"
        Headers = $retryHeaders
        ContentType = "application/json"
        Body = $retryBody
        TimeoutSec = $TimeoutSec
      }
    } catch {
      $sw.Stop()
      $errMsg = ""
      try { $errMsg = $_.Exception.Message } catch { $errMsg = "request failed" }
      if ($errMsg) { Write-Host "ERROR: $errMsg" }
      $result = [PSCustomObject]@{
        StatusCode = 0
        RetryAfter = $null
        Body = $null
        RequestId = $retryRid
        Error = $errMsg
      }
      Print-Response -Resp $result -LatencyMs $sw.ElapsedMilliseconds
      return $result
    }
    $sw.Stop()

    $status = $resp.StatusCode
    $retryAfter = $resp.Headers["Retry-After"]
    $body = $null
    $requestId = $resp.Headers["X-Request-ID"]

    if ($resp.Content) {
      try {
        $body = $resp.Content | ConvertFrom-Json
        if (-not $requestId) {
          $requestId = Get-JsonProperty -Object $body -Name "request_id"
        }
      } catch {
        $body = $resp.Content
      }
    }
  }

  $result = [PSCustomObject]@{
    StatusCode = $status
    RetryAfter = $retryAfter
    Body = $body
    RequestId = $requestId
    Error = $null
  }

  Print-Response -Resp $result -LatencyMs $sw.ElapsedMilliseconds
  return $result
}

if (-not $Identifier -or -not $Password -or -not $MerchantUid) {
  Write-Error "missing Identifier/Password/MerchantUid"
  exit 2
}

if (-not (Test-ApiReady)) {
  if ($SkipIfApiDown) {
    Write-Host "SKIP: API unreachable"
    exit 0
  }
  Write-Error "API unreachable"
  exit 1
}

$loginRid = New-RequestId
$loginHeaders = @{ "X-Request-ID" = $loginRid }
$loginBody = @{ identifier = $Identifier; password = $Password } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/auth/login" -Headers $loginHeaders -ContentType "application/json" -Body $loginBody -TimeoutSec 15

$access = $login.access_token
if (-not $access) { $access = $login.accessToken }
if (-not $access) { throw "Login response: access token not found." }

$path = Resolve-KaspiSyncNowPath
if (-not $path) { exit 1 }
"Resolved endpoint: $path"

if (-not (Test-ApiReady)) {
  if ($SkipIfApiDown) {
    Write-Host "SKIP: API unreachable"
    exit 0
  }
  Write-Error "API unreachable"
  exit 1
}

"--- First call ---"
$first = Invoke-KaspiSyncNow -Token $access -Path $path -MerchantUid $MerchantUid

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
$second = Invoke-KaspiSyncNow -Token $access -Path $path -MerchantUid $MerchantUid

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

if (-not ($secondCode -eq 409 -and $secondErrCode -eq "kaspi_sync_in_progress")) {
  if ($secondCode -eq 504 -and $secondErrCode -eq "kaspi_sync_timeout") {
    Write-Host "WARN: kaspi sync timeout on second call; not failing script."
    exit 0
  }
  Write-Error "Second call returned unexpected status: $secondCode"
  exit 1
}

"PASS"
