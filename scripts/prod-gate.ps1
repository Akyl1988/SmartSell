# Example:
# pwsh -NoProfile -File .\scripts\prod-gate.ps1 -BaseUrl "http://127.0.0.1:8000" -Identifier "77078342842" -Password "S3curePass!2026"
param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Identifier = "",
  [string]$Password = ""
)

$ErrorActionPreference = "Stop"

function _Require-Creds() {
  if ([string]::IsNullOrWhiteSpace($Identifier)) {
    $script:Identifier = Read-Host -Prompt "Identifier"
  }
  if (-not [string]::IsNullOrWhiteSpace($env:SMARTSELL_PASSWORD)) {
    $script:Password = $env:SMARTSELL_PASSWORD
  } elseif ([string]::IsNullOrWhiteSpace($Password)) {
    $secure = Read-Host -Prompt "Password" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
      $script:Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
      if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
      }
    }
  }
}

function Run-Step([string]$title, [scriptblock]$action) {
  Write-Host $title
  $global:LASTEXITCODE = 0
  & $action
  if ($LASTEXITCODE -ne 0) {
    throw "$title failed (exit code $LASTEXITCODE)"
  }
}

function Invoke-Http {
  param(
    [Parameter(Mandatory = $true)][string]$Method,
    [Parameter(Mandatory = $true)][string]$Url,
    [object]$Body = $null,
    [hashtable]$Headers = $null
  )

  $params = @{
    Uri = $Url
    Method = $Method
    TimeoutSec = 20
  }
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
    $params.SkipHttpErrorCheck = $true
  }
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("UseBasicParsing")) {
    $params.UseBasicParsing = $true
  }
  if ($Headers) { $params.Headers = $Headers }
  if ($Body -ne $null) {
    $params.ContentType = "application/json"
    $params.Body = ($Body | ConvertTo-Json -Depth 10)
  }

  try {
    $resp = Invoke-WebRequest @params
    Write-Host ("{0} {1} => {2}" -f $Method, $Url, $resp.StatusCode)
    return $resp
  } catch {
    $webResp = $_.Exception.Response
    if ($null -ne $webResp) {
      $statusCode = $webResp.StatusCode.value__
      $content = ""
      try {
        $reader = New-Object System.IO.StreamReader($webResp.GetResponseStream())
        $content = $reader.ReadToEnd()
        $reader.Close()
      } catch { }
      Write-Host ("{0} {1} => {2}" -f $Method, $Url, $statusCode)
      return [pscustomobject]@{ StatusCode = $statusCode; Content = $content }
    }
    throw
  }
}

try {
  Run-Step "RUFF" {
python -m ruff format --check .
    python -m ruff check .
  }

  $oldDbUrl = $env:DATABASE_URL
  $hadDbUrl = -not [string]::IsNullOrWhiteSpace($oldDbUrl)
  try {
    Run-Step "ALEMBIC SMOKE" {
      $env:DATABASE_URL = "postgresql+asyncpg://postgres:admin123@127.0.0.1:5432/smartsell_test"
      alembic upgrade head
      alembic downgrade -1
      alembic upgrade head
    }
  } finally {
    if ($hadDbUrl) {
      $env:DATABASE_URL = $oldDbUrl
    } else {
      Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    }
  }

  Run-Step "PYTEST" {
    python -m pytest -q
  }

  _Require-Creds

  Run-Step "LOGIN/REFRESH CHECK" {
    $loginUrl = "$BaseUrl/api/v1/auth/login"
    $refreshUrl = "$BaseUrl/api/v1/auth/refresh"
    $payload = @{ identifier = $Identifier; password = $Password }
    $resp = Invoke-Http -Method "POST" -Url $loginUrl -Body $payload
    if ($resp.StatusCode -lt 200 -or $resp.StatusCode -ge 300) {
      if ($resp.Content) {
        Write-Host $resp.Content
      }
      exit 1
    }

    $json = $null
    try {
      $json = $resp.Content | ConvertFrom-Json
    } catch {
      Write-Error "Login response is not valid JSON"
      if ($resp.Content) {
        Write-Host $resp.Content
      }
      exit 1
    }

    $refreshToken = $json.refresh_token
    if (-not $refreshToken) { $refreshToken = $json.refreshToken }
    if (-not $refreshToken) {
      Write-Error "Login response missing refresh_token"
      exit 1
    }

    $refreshResp = Invoke-Http -Method "POST" -Url $refreshUrl -Body @{ refresh_token = $refreshToken }
    if ($refreshResp.StatusCode -lt 200 -or $refreshResp.StatusCode -ge 300) {
      if ($refreshResp.Content) {
        Write-Host $refreshResp.Content
      }
      exit 1
    }
  }

  Run-Step "AUTH NEGATIVE SMOKE" {
    $endpoints = @(
      "$BaseUrl/api/v1/auth/me",
      "$BaseUrl/api/v1/users/me"
    )

    foreach ($url in $endpoints) {
      $noAuth = Invoke-Http -Method "GET" -Url $url
      if ($noAuth.StatusCode -notin 401, 403) {
        Write-Warning ("Unexpected status for {0} without auth: {1}" -f $url, $noAuth.StatusCode)
        if ($noAuth.Content) { Write-Warning $noAuth.Content }
      }

      $badAuth = Invoke-Http -Method "GET" -Url $url -Headers @{ Authorization = "Bearer invalid.token.value" }
      if ($badAuth.StatusCode -notin 401, 403) {
        Write-Warning ("Unexpected status for {0} with invalid token: {1}" -f $url, $badAuth.StatusCode)
        if ($badAuth.Content) { Write-Warning $badAuth.Content }
      }
    }
  }

  Run-Step "SMOKE CORE" {
    pwsh -NoProfile -File .\scripts\smoke-core.ps1 -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password
  }

  $smokeScript = ".\scripts\smoke-kaspi-sync-now.ps1"
  $hasSmokeScript = Test-Path $smokeScript
  $hasEnv = (
    -not [string]::IsNullOrWhiteSpace($env:SMARTSELL_IDENTIFIER) -and
    -not [string]::IsNullOrWhiteSpace($env:SMARTSELL_PASSWORD) -and
    -not [string]::IsNullOrWhiteSpace($env:KASPI_MERCHANT_UID)
  )
  $apiOk = $false

  if ($hasSmokeScript -and $hasEnv) {
    try {
      $pingParams = @{ Uri = "$BaseUrl/openapi.json"; Method = "GET"; TimeoutSec = 2 }
      if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
        $pingParams.SkipHttpErrorCheck = $true
      }
      if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("UseBasicParsing")) {
        $pingParams.UseBasicParsing = $true
      }
      $ping = Invoke-WebRequest @pingParams
      if ($ping.StatusCode -ge 200 -and $ping.StatusCode -lt 300) {
        $apiOk = $true
      }
    } catch {
      $apiOk = $false
    }
  }

  if ($hasSmokeScript -and $hasEnv -and $apiOk) {
    Run-Step "SMOKE-KASPI-SYNC-NOW" {
      pwsh -NoProfile -File $smokeScript -BaseUrl $BaseUrl -Identifier $env:SMARTSELL_IDENTIFIER -Password $env:SMARTSELL_PASSWORD -MerchantUid $env:KASPI_MERCHANT_UID -TimeoutSec 30 -ProbeTimeoutSec 2 -SkipIfApiDown
    }
  } else {
    Write-Host "SKIP: smoke-kaspi-sync-now (missing env or API not running)"
  }

  Write-Host "DONE OK"
} catch {
  Write-Host $_
  exit 1
}

