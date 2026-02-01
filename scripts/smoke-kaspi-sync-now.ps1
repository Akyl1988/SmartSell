Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --------- User-configurable variables ---------
$BaseUrl    = "http://127.0.0.1:8000"
$Identifier = $env:SMARTSELL_IDENTIFIER
$Password   = $env:SMARTSELL_PASSWORD
$MerchantUid = $env:KASPI_MERCHANT_UID

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
    if (-not $Resp) {
      Write-Host "STATUS: <null>"
      return
    }

    $status = Get-JsonProperty -Object $Resp -Name "StatusCode"
    $retryAfter = Get-JsonProperty -Object $Resp -Name "RetryAfter"
    $body = Get-JsonProperty -Object $Resp -Name "Body"
    $requestId = Get-JsonProperty -Object $Resp -Name "RequestId"

    Write-Host "STATUS: $status"
    Write-Host "LATENCY_MS: $LatencyMs"
    if ($requestId) { Write-Host "REQUEST_ID: $requestId" }
    if ($retryAfter) { Write-Host "RETRY_AFTER: $retryAfter" }
    if ($body) { Write-Host ($body | ConvertTo-Json -Depth 50) }
  } catch {
    Write-Host "WARN: failed to print response"
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
    $resp = Invoke-WebRequest -Method Post -Uri "$BaseUrl$Path" -Headers $headers -ContentType "application/json" -Body $null -SkipHttpErrorCheck -TimeoutSec 15
  } catch {
    $sw.Stop()
    Print-Response -Resp $null -LatencyMs $sw.ElapsedMilliseconds
    return [PSCustomObject]@{
      StatusCode = $null
      RetryAfter = $null
      Body = $null
      RequestId = $rid
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
      $resp = Invoke-WebRequest -Method Post -Uri "$BaseUrl$Path" -Headers $retryHeaders -ContentType "application/json" -Body $retryBody -SkipHttpErrorCheck -TimeoutSec 15
    } catch {
      $sw.Stop()
      $result = [PSCustomObject]@{
        StatusCode = $null
        RetryAfter = $null
        Body = $null
        RequestId = $retryRid
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
  }

  Print-Response -Resp $result -LatencyMs $sw.ElapsedMilliseconds
  return $result
}

if (-not $Identifier -or -not $Password) {
  throw "SMARTSELL_IDENTIFIER and SMARTSELL_PASSWORD must be set (env vars or edit script)."
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

"--- First call ---"
$first = Invoke-KaspiSyncNow -Token $access -Path $path -MerchantUid $MerchantUid

if (-not $first) {
  Write-Error "First call failed to return a response"
  exit 1
}

if ($first.StatusCode -ge 500) {
  Write-Error "First call returned server error: $($first.StatusCode)"
  exit 1
}

if ($first.StatusCode -eq 429) {
  "WARN: rate limited; not failing script."
  exit 0
}

"--- Second call ---"
$second = Invoke-KaspiSyncNow -Token $access -Path $path -MerchantUid $MerchantUid

if (-not $second) {
  Write-Error "Second call failed to return a response"
  exit 1
}

if ($second.StatusCode -ge 500) {
  Write-Error "Second call returned server error: $($second.StatusCode)"
  exit 1
}

if ($second.StatusCode -eq 429) {
  "WARN: rate limited on second call; not failing script."
  exit 0
}

"PASS"
