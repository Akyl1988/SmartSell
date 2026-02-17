Set-StrictMode -Version Latest
$script:SmartsellAccessToken = $null
$script:SmartsellRefreshToken = $null
$script:SmartsellCachePath = $null

function Mask-Secret([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
  if ($Value.Length -le 8) { return ("*" * $Value.Length) }
  return ($Value.Substring(0,4) + "..." + $Value.Substring($Value.Length-4))
}

function Get-SmokeCachePath {
  if ($script:SmartsellCachePath) { return $script:SmartsellCachePath }
  $root = $PSScriptRoot
  if (-not $root) { $root = (Get-Location).Path }
  $script:SmartsellCachePath = Join-Path $root ".smoke-cache.json"
  return $script:SmartsellCachePath
}

function Read-SmokeCache {
  $path = Get-SmokeCachePath
  if (-not (Test-Path $path)) { return @{} }
  try {
    $raw = Get-Content -LiteralPath $path -Raw
    if (-not $raw) { return @{} }
    $data = $raw | ConvertFrom-Json
    if ($data -is [hashtable]) { return $data }
    $ht = @{}
    foreach ($p in $data.PSObject.Properties) { $ht[$p.Name] = $p.Value }
    return $ht
  } catch {
    return @{}
  }
}

function Clear-SmokeAuthCache {
  param([string]$BaseUrl)
  $path = Get-SmokeCachePath
  if (-not (Test-Path $path)) { return $false }
  if (-not $BaseUrl) {
    Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue
    return $true
  }

  $cache = Read-SmokeCache
  if ($cache.ContainsKey($BaseUrl)) {
    $cache.Remove($BaseUrl) | Out-Null
    return (Write-SmokeCache -Data $cache)
  }
  return $false
}

function Write-SmokeCache {
  param([hashtable]$Data)
  $path = Get-SmokeCachePath
  try {
    $json = $Data | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath $path -Value $json -Encoding UTF8
  } catch {
    return $false
  }
  return $true
}

function Normalize-JwtToken {
  param([string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
  $token = ([string]$Value).Trim()
  if ($token -match '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$') {
    return $token
  }
  return $null
}

function Resolve-TokenField {
  param(
    [object]$Entry,
    [string[]]$Names
  )
  if (-not $Entry) { return $null }
  foreach ($name in $Names) {
    $prop = $Entry.PSObject.Properties[$name]
    if ($prop -and -not [string]::IsNullOrWhiteSpace([string]$prop.Value)) {
      return @{ name = $name; value = $prop.Value }
    }
  }
  return $null
}

function Load-SmartsellTokensFromCache {
  param([string]$BaseUrl)
  if (-not $BaseUrl) { return $null }
  $cache = Read-SmokeCache
  if (-not $cache.ContainsKey($BaseUrl)) { return $null }
  $entry = $cache[$BaseUrl]
  if (-not $entry) { return $null }
  $accessField = Resolve-TokenField -Entry $entry -Names @("access", "access_token", "token")
  $refreshField = Resolve-TokenField -Entry $entry -Names @("refresh", "refresh_token")
  $access = $null
  $refresh = $null
  if ($accessField -and $accessField.value) { $access = Normalize-JwtToken -Value $accessField.value }
  if ($refreshField -and $refreshField.value) { $refresh = Normalize-JwtToken -Value $refreshField.value }
  if (-not $access -and -not $refresh) { return $null }
  return @{
    access = $access
    refresh = $refresh
  }
}

function Save-SmartsellTokensToCache {
  param(
    [string]$BaseUrl,
    [string]$AccessToken,
    [string]$RefreshToken
  )
  if (-not $BaseUrl) { return $false }
  $cache = Read-SmokeCache
  $entry = @{}
  if ($AccessToken) { $entry.access = $AccessToken }
  if ($RefreshToken) { $entry.refresh = $RefreshToken }
  $entry.updated_at = (Get-Date).ToString("o")
  $cache[$BaseUrl] = $entry
  return (Write-SmokeCache -Data $cache)
}

function Get-SmokeCacheEntry {
  param([string]$BaseUrl)
  if (-not $BaseUrl) { return $null }
  $cache = Read-SmokeCache
  if (-not $cache.ContainsKey($BaseUrl)) { return $null }
  return $cache[$BaseUrl]
}

function Save-SmokeCacheEntry {
  param(
    [string]$BaseUrl,
    [hashtable]$Entry
  )
  if (-not $BaseUrl) { return $false }
  $cache = Read-SmokeCache
  $cache[$BaseUrl] = $Entry
  return (Write-SmokeCache -Data $cache)
}

function Test-SmokeApiUp {
  param(
    [string]$BaseUrl,
    [int]$TimeoutSec = 3
  )
  if (-not $BaseUrl) { throw "BaseUrl is required" }

  $targets = @(
    "$BaseUrl/api/v1/wallet/health",
    "$BaseUrl/api/v1/health"
  )

  foreach ($url in $targets) {
    try {
      Invoke-WebRequestSafe -Params @{
        Method = "GET"
        Uri = $url
        TimeoutSec = $TimeoutSec
      } | Out-Null
      return $true
    } catch {
      $ex = $_.Exception
      $msg = ""
      try { $msg = $ex.Message } catch { }
      $isConnRefused = ($ex -is [System.Net.WebException]) -or ($msg -match "refused|No connection could be made|actively refused")
      if ($isConnRefused) {
        throw "API is not running at $BaseUrl. Start it via scripts/run-api-dev.ps1 (or dev.ps1 api)."
      }
    }
  }

  return $true
}

function Invoke-SmokeRefresh {
  param(
    [string]$BaseUrl,
    [string]$RefreshToken,
    [int]$TimeoutSec = 20
  )
  if (-not $BaseUrl) { throw "BaseUrl is required" }
  if (-not $RefreshToken) { throw "No refresh token cached; run smoke-auth or login first" }

  Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null

  $envToken = $null
  if ($env:SMARTSELL_ACCESS_TOKEN) { $envToken = Normalize-JwtToken -Value $env:SMARTSELL_ACCESS_TOKEN }
  if (-not $envToken -and $env:ACCESS_TOKEN) { $envToken = Normalize-JwtToken -Value $env:ACCESS_TOKEN }
  if ($envToken) {
    Set-SmartsellTokens -AccessToken $envToken -RefreshToken $null -BaseUrl $BaseUrl
    return @{ Authorization = "Bearer $envToken" }
  }

  $refreshUrl = "$BaseUrl/api/v1/auth/refresh"
  $body = @{ refresh_token = $RefreshToken } | ConvertTo-Json
  $resp = Invoke-WebRequestSafe -Params @{
    Method = "POST"
    Uri = $refreshUrl
    TimeoutSec = $TimeoutSec
    ContentType = "application/json"
    Body = $body
  }

  $status = $resp.StatusCode
  if ($status -lt 200 -or $status -ge 300) {
    $text = $resp.Content
    throw "refresh failed: status=$status body=$text"
  }

  $payload = $null
  try {
    $payload = $resp.Content | ConvertFrom-Json
  } catch {
    throw "refresh failed: invalid json"
  }

  $access = $payload.access_token
  if (-not $access) { $access = $payload.accessToken }
  $refresh = $payload.refresh_token
  if (-not $refresh) { $refresh = $payload.refreshToken }

  $access = Normalize-JwtToken -Value $access
  $refresh = Normalize-JwtToken -Value $refresh
  if (-not $access) { throw "refresh failed: missing access token" }
  if (-not $refresh) { throw "refresh failed: missing refresh token" }

  return @{
    access = $access
    refresh = $refresh
  }
}

function Get-SmokeAuthHeader {
  param(
    [string]$BaseUrl,
    [switch]$ForceRefresh
  )
  if (-not $BaseUrl) {
    $BaseUrl = $env:BASE_URL
    if (-not $BaseUrl) { $BaseUrl = $env:SMARTSELL_BASE_URL }
    if (-not $BaseUrl) {
      if (Is-DevEnvironment) {
        throw "BaseUrl is required (set BASE_URL or SMARTSELL_BASE_URL)"
      }
      throw "BaseUrl is required"
    }
  }

  Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null

  # Returns headers for Invoke-WebRequest -Headers (not curl -H).
  $entry = Get-SmokeCacheEntry -BaseUrl $BaseUrl
  $accessField = Resolve-TokenField -Entry $entry -Names @("access", "access_token", "token")
  $refreshField = Resolve-TokenField -Entry $entry -Names @("refresh", "refresh_token")
  if ($accessField) {
    Write-Host ("[INFO] smoke-cache access field: {0}" -f $accessField.name)
  } elseif ($entry) {
    $keys = @($entry.PSObject.Properties.Name) -join ","
    Write-Host ("[WARN] smoke-cache entry missing access token field (keys={0})" -f $keys)
  }
  $access = $null
  $refresh = $null
  if ($accessField -and $accessField.value) { $access = Normalize-JwtToken -Value $accessField.value }
  if ($refreshField -and $refreshField.value) {
    Write-Host ("[INFO] smoke-cache refresh field: {0}" -f $refreshField.name)
    $refresh = Normalize-JwtToken -Value $refreshField.value
  }

  function Invoke-AuthMe([string]$Token) {
    if (-not $Token) {
      return [PSCustomObject]@{ StatusCode = 401; Body = @{ code = "AUTH_REQUIRED" } }
    }
    $resp = Invoke-WebRequestSafe -Params @{
      Method = "GET"
      Uri = "$BaseUrl/api/v1/auth/me"
      Headers = @{ Authorization = "Bearer $Token" }
      TimeoutSec = 20
    }
    $status = $resp.StatusCode
    $body = $null
    if ($resp.Content) {
      try { $body = $resp.Content | ConvertFrom-Json } catch { $body = $resp.Content }
    }
    return [PSCustomObject]@{ StatusCode = $status; Body = $body }
  }

  function Get-ErrorCode([object]$Body) {
    if (-not $Body -or $Body -is [string]) { return $null }
    $code = $Body.PSObject.Properties["code"]
    if ($code) { return $code.Value }
    $detail = $Body.PSObject.Properties["detail"]
    if ($detail) { return $detail.Value }
    $errs = $Body.PSObject.Properties["errors"]
    if ($errs -and $errs.Value -and $errs.Value.Count -gt 0) {
      $err0 = $errs.Value[0]
      $eCode = $err0.PSObject.Properties["code"]
      if ($eCode) { return $eCode.Value }
      $eDetail = $err0.PSObject.Properties["detail"]
      if ($eDetail) { return $eDetail.Value }
    }
    return $null
  }

  function Try-RefreshTokens([string]$RefreshToken) {
    if (-not $RefreshToken) { return $null }
    $refreshUrl = "$BaseUrl/api/v1/auth/refresh"
    $body = @{ refresh_token = $RefreshToken } | ConvertTo-Json
    $resp = Invoke-WebRequestSafe -Params @{
      Method = "POST"
      Uri = $refreshUrl
      TimeoutSec = 20
      ContentType = "application/json"
      Body = $body
    }
    if ($resp.StatusCode -lt 200 -or $resp.StatusCode -ge 300) {
      return @{
        ok = $false
        status = $resp.StatusCode
        body = $resp.Content
      }
    }
    $payload = $null
    try { $payload = $resp.Content | ConvertFrom-Json } catch { $payload = $null }
    if (-not $payload) { return @{ ok = $false; status = 0; body = "invalid_json" } }
    $access = Normalize-JwtToken -Value ($payload.access_token ?? $payload.accessToken)
    $refreshNew = Normalize-JwtToken -Value ($payload.refresh_token ?? $payload.refreshToken)
    if (-not $access -or -not $refreshNew) { return @{ ok = $false; status = 0; body = "missing_tokens" } }
    return @{ ok = $true; access = $access; refresh = $refreshNew }
  }

  function Login-WithEnv {
    $identifier = $env:SMARTSELL_IDENTIFIER
    $password = $env:SMARTSELL_PASSWORD
    if (-not $identifier -or -not $password) {
      throw "SMARTSELL_IDENTIFIER/SMARTSELL_PASSWORD are required for login fallback"
    }
    $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $identifier -Password $password -TimeoutSec 20
    return $tokens
  }

  if ($ForceRefresh -or -not $access) {
    $refreshResult = Try-RefreshTokens -RefreshToken $refresh
    if ($refreshResult -and $refreshResult.ok) {
      $access = $refreshResult.access
      $refresh = $refreshResult.refresh
      $entry = @{ access = $access; refresh = $refresh; updated_at = (Get-Date).ToString("o") }
      Save-SmokeCacheEntry -BaseUrl $BaseUrl -Entry $entry | Out-Null
      $me = Invoke-AuthMe -Token $access
      if ($me.StatusCode -eq 200) { return @{ Authorization = "Bearer $access" } }
    } else {
      if ($refreshResult -and $refreshResult.status -eq 401) {
        Clear-SmokeAuthCache -BaseUrl $BaseUrl | Out-Null
      }
    }

    $tokens = Login-WithEnv
    $access = $tokens.access
    $refresh = $tokens.refresh
    $entry = @{ access = $access; refresh = $refresh; updated_at = (Get-Date).ToString("o") }
    Save-SmokeCacheEntry -BaseUrl $BaseUrl -Entry $entry | Out-Null
    $me = Invoke-AuthMe -Token $access
    if ($me.StatusCode -ne 200) {
      $text = $me.Body | ConvertTo-Json -Depth 10
      throw "auth/me failed after login: status=$($me.StatusCode) body=$text"
    }
    return @{ Authorization = "Bearer $access" }
  }

  $me = Invoke-AuthMe -Token $access
  if ($me.StatusCode -eq 200) {
    return @{ Authorization = "Bearer $access" }
  }

  $errCode = Get-ErrorCode -Body $me.Body
  if (Test-AuthExpired -StatusCode $me.StatusCode -Body $me.Body -or $errCode -match "USER_NOT_FOUND|INVALID_TOKEN") {
    $refreshResult = Try-RefreshTokens -RefreshToken $refresh
    if ($refreshResult -and $refreshResult.ok) {
      $access = $refreshResult.access
      $refresh = $refreshResult.refresh
      $entry = @{ access = $access; refresh = $refresh; updated_at = (Get-Date).ToString("o") }
      Save-SmokeCacheEntry -BaseUrl $BaseUrl -Entry $entry | Out-Null
      $me2 = Invoke-AuthMe -Token $access
      if ($me2.StatusCode -eq 200) { return @{ Authorization = "Bearer $access" } }
    } elseif ($refreshResult -and $refreshResult.status -eq 401) {
      Clear-SmokeAuthCache -BaseUrl $BaseUrl | Out-Null
    }

    $tokens = Login-WithEnv
    $access = $tokens.access
    $refresh = $tokens.refresh
    $entry = @{ access = $access; refresh = $refresh; updated_at = (Get-Date).ToString("o") }
    Save-SmokeCacheEntry -BaseUrl $BaseUrl -Entry $entry | Out-Null
    $me2 = Invoke-AuthMe -Token $access
    if ($me2.StatusCode -ne 200) {
      $text = $me2.Body | ConvertTo-Json -Depth 10
      throw "auth/me failed after login: status=$($me2.StatusCode) body=$text"
    }
    return @{ Authorization = "Bearer $access" }
  }

  $fallback = $me.Body | ConvertTo-Json -Depth 10
  throw "auth/me failed: status=$($me.StatusCode) body=$fallback"
}

function Get-SmokeErrorCode {
  param([object]$Body)
  if (-not $Body -or $Body -is [string]) { return $null }
  $code = $Body.PSObject.Properties["code"]
  if ($code) { return $code.Value }
  $detail = $Body.PSObject.Properties["detail"]
  if ($detail) { return $detail.Value }
  $errs = $Body.PSObject.Properties["errors"]
  if ($errs -and $errs.Value -and $errs.Value.Count -gt 0) {
    $err0 = $errs.Value[0]
    $eCode = $err0.PSObject.Properties["code"]
    if ($eCode) { return $eCode.Value }
    $eDetail = $err0.PSObject.Properties["detail"]
    if ($eDetail) { return $eDetail.Value }
  }
  return $null
}

function Ensure-SmartsellAuth {
  param(
    [string]$BaseUrl,
    [string]$Identifier = $null,
    [string]$Password = $null,
    [string]$AccessToken = $null,
    [string]$RefreshToken = $null
  )
  if (-not $BaseUrl) {
    $BaseUrl = $env:BASE_URL
    if (-not $BaseUrl) { $BaseUrl = $env:SMARTSELL_BASE_URL }
    if (-not $BaseUrl) { throw "BaseUrl is required" }
  }

  Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null

  $envAccess = $null
  if ($env:SMARTSELL_ACCESS_TOKEN) { $envAccess = Normalize-JwtToken -Value $env:SMARTSELL_ACCESS_TOKEN }
  if (-not $envAccess -and $env:ACCESS_TOKEN) { $envAccess = Normalize-JwtToken -Value $env:ACCESS_TOKEN }
  if ($envAccess) { $AccessToken = $envAccess }

  $envRefresh = $null
  if ($env:SMARTSELL_REFRESH_TOKEN) { $envRefresh = Normalize-JwtToken -Value $env:SMARTSELL_REFRESH_TOKEN }
  if ($envRefresh) { $RefreshToken = $envRefresh }

  if (-not $AccessToken -or -not $RefreshToken) {
    $cached = Load-SmartsellTokensFromCache -BaseUrl $BaseUrl
    if ($cached) {
      if (-not $AccessToken -and $cached.access) { $AccessToken = $cached.access }
      if (-not $RefreshToken -and $cached.refresh) { $RefreshToken = $cached.refresh }
    }
  }

  function Invoke-AuthMe([string]$Token) {
    if (-not $Token) {
      return [PSCustomObject]@{ StatusCode = 401; Body = @{ code = "AUTH_REQUIRED" } }
    }
    $resp = Invoke-WebRequestSafe -Params @{
      Method = "GET"
      Uri = "$BaseUrl/api/v1/auth/me"
      Headers = @{ Authorization = "Bearer $Token" }
      TimeoutSec = 20
    }
    $body = $null
    if ($resp.Content) {
      try { $body = $resp.Content | ConvertFrom-Json } catch { $body = $resp.Content }
    }
    return [PSCustomObject]@{ StatusCode = $resp.StatusCode; Body = $body }
  }

  function Try-Refresh([string]$Token) {
    if (-not $Token) { return $null }
    Write-Host "[INFO] Refreshing access token"
    $refreshUrl = "$BaseUrl/api/v1/auth/refresh"
    $body = @{ refresh_token = $Token } | ConvertTo-Json
    $resp = Invoke-WebRequestSafe -Params @{
      Method = "POST"
      Uri = $refreshUrl
      TimeoutSec = 20
      ContentType = "application/json"
      Body = $body
    }
    if ($resp.StatusCode -lt 200 -or $resp.StatusCode -ge 300) {
      return @{ ok = $false; status = $resp.StatusCode; body = $resp.Content }
    }
    $payload = $null
    try { $payload = $resp.Content | ConvertFrom-Json } catch { $payload = $null }
    if (-not $payload) { return @{ ok = $false; status = 0; body = "invalid_json" } }
    $accessNew = Normalize-JwtToken -Value ($payload.access_token ?? $payload.accessToken)
    $refreshNew = Normalize-JwtToken -Value ($payload.refresh_token ?? $payload.refreshToken)
    if (-not $accessNew -or -not $refreshNew) { return @{ ok = $false; status = 0; body = "missing_tokens" } }
    Set-SmartsellTokens -AccessToken $accessNew -RefreshToken $refreshNew -BaseUrl $BaseUrl
    Write-Host "[OK] Token refreshed"
    return @{ ok = $true; access = $accessNew; refresh = $refreshNew }
  }

  if (-not $Identifier) { $Identifier = $env:SMARTSELL_IDENTIFIER }
  if (-not $Password) { $Password = $env:SMARTSELL_PASSWORD }

  if ($AccessToken) {
    $me = Invoke-AuthMe -Token $AccessToken
    $errCode = Get-SmokeErrorCode -Body $me.Body
    if (-not (Test-AuthExpired -StatusCode $me.StatusCode -Body $me.Body) -and -not ($errCode -match "INVALID_TOKEN|TOKEN_EXPIRED")) {
      Set-SmartsellTokens -AccessToken $AccessToken -RefreshToken $RefreshToken -BaseUrl $BaseUrl
      return @{ Authorization = "Bearer $AccessToken" }
    }
  }

  $refreshResult = Try-Refresh -Token $RefreshToken
  if ($refreshResult -and $refreshResult.ok) {
    return @{ Authorization = "Bearer $($refreshResult.access)" }
  }

  if ($Identifier -and $Password) {
    $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password -TimeoutSec 20
    return @{ Authorization = "Bearer $($tokens.access)" }
  }

  throw "No valid auth token; set SMARTSELL_ACCESS_TOKEN/SMARTSELL_REFRESH_TOKEN or SMARTSELL_IDENTIFIER/SMARTSELL_PASSWORD"
}

function Invoke-WebRequestSafe {
  param([hashtable]$Params)
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
    $Params.SkipHttpErrorCheck = $true
  }
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("UseBasicParsing")) {
    $Params.UseBasicParsing = $true
  }
  return Invoke-WebRequest @Params
}

function Resolve-ProfileValue {
  param(
    [object]$Profile,
    [string]$Name
  )
  if (-not $Profile) { return $null }
  $direct = $Profile.PSObject.Properties[$Name]
  if ($direct) { return $direct.Value }
  $user = $Profile.PSObject.Properties["user"]
  if ($user -and $user.Value) {
    $uval = $user.Value
    $uv = $uval.PSObject.Properties[$Name]
    if ($uv) { return $uv.Value }
  }
  $company = $Profile.PSObject.Properties["company"]
  if ($company -and $company.Value) {
    $cval = $company.Value
    $cv = $cval.PSObject.Properties[$Name]
    if ($cv) { return $cv.Value }
  }
  return $null
}

function Get-BaseUrlFromUrl {
  param([string]$Url)
  try {
    $uri = [uri]$Url
    $base = "$($uri.Scheme)://$($uri.Host)"
    if (-not $uri.IsDefaultPort) { $base = "${base}:$($uri.Port)" }
    return $base
  } catch {
    return ""
  }
}

function Set-SmartsellTokens {
  param(
    [string]$AccessToken,
    [string]$RefreshToken,
    [string]$BaseUrl = $null
  )
  $normalizedAccess = Normalize-JwtToken -Value $AccessToken
  $normalizedRefresh = Normalize-JwtToken -Value $RefreshToken
  if ($normalizedAccess) {
    $AccessToken = $normalizedAccess
    $script:SmartsellAccessToken = $AccessToken
    $env:SMARTSELL_ACCESS_TOKEN = $AccessToken
  }
  if ($normalizedRefresh) {
    $RefreshToken = $normalizedRefresh
    $script:SmartsellRefreshToken = $RefreshToken
    $env:SMARTSELL_REFRESH_TOKEN = $RefreshToken
  }
  if ($BaseUrl -and ($normalizedAccess -or $normalizedRefresh)) {
    Save-SmartsellTokensToCache -BaseUrl $BaseUrl -AccessToken $normalizedAccess -RefreshToken $normalizedRefresh | Out-Null
  }
}

function Get-SmartsellTokens {
  param(
    [string]$BaseUrl,
    [string]$Identifier,
    [string]$Password,
    [int]$TimeoutSec = 20
  )
  Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null
  $loginUrl = "$BaseUrl/api/v1/auth/login"
  $login = Invoke-RestMethod -Method POST -Uri $loginUrl -TimeoutSec $TimeoutSec -ContentType "application/json" -Body (@{ identifier = $Identifier; password = $Password } | ConvertTo-Json)

  $access = $login.access_token
  if (-not $access) { $access = $login.accessToken }
  $refresh = $login.refresh_token
  if (-not $refresh) { $refresh = $login.refreshToken }

  if (-not $access -or -not $refresh) {
    throw "Login response missing access_token/refresh_token"
  }

  $access = Normalize-JwtToken -Value $access
  $refresh = Normalize-JwtToken -Value $refresh
  if (-not $access -or -not $refresh) {
    throw "Login response missing valid access_token/refresh_token"
  }

  Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh -BaseUrl $BaseUrl

  $me = $null
  try {
    $resp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/auth/me" -TimeoutSec $TimeoutSec -AccessToken $access -RefreshToken $refresh
    if ($resp -and $resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
      $me = $resp.Body
    }
  } catch {
    $me = $null
  }

  return @{
    access = $access
    refresh = $refresh
    user = $me
  }
}

function Refresh-SmartsellAccessToken {
  param(
    [string]$BaseUrl,
    [string]$RefreshToken,
    [int]$TimeoutSec = 20
  )
  if (-not $RefreshToken) { return $null }
  Test-SmokeApiUp -BaseUrl $BaseUrl -TimeoutSec 3 | Out-Null
  $refreshUrl = "$BaseUrl/api/v1/auth/refresh"
  $body = @{ refresh_token = $RefreshToken } | ConvertTo-Json
  $data = Invoke-RestMethod -Method POST -Uri $refreshUrl -TimeoutSec $TimeoutSec -ContentType "application/json" -Body $body

  $access = $data.access_token
  if (-not $access) { $access = $data.accessToken }
  $refresh = $data.refresh_token
  if (-not $refresh) { $refresh = $data.refreshToken }

  if (-not $access) { return $null }
  $access = Normalize-JwtToken -Value $access
  $refresh = Normalize-JwtToken -Value $refresh
  if (-not $access) { return $null }
  Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh -BaseUrl $BaseUrl
  return @{
    access = $access
    refresh = $refresh
  }
}

function Test-AuthExpired {
  param(
    [int]$StatusCode,
    [object]$Body
  )
  if ($StatusCode -eq 401) { return $true }
  if (-not $Body -or $Body -is [string]) { return $false }

  function Get-PropValue([object]$Obj, [string]$Name) {
    if (-not $Obj) { return $null }
    $prop = $Obj.PSObject.Properties[$Name]
    if ($prop) { return $prop.Value }
    return $null
  }

  $code = Get-PropValue -Obj $Body -Name "code"
  $detail = Get-PropValue -Obj $Body -Name "detail"
  if ($code -and ($code -match "INVALID_TOKEN|TOKEN_EXPIRED|token_expired|access_token_expired")) { return $true }
  if ($detail -and ($detail -match "token expired|invalid token|INVALID_TOKEN|TOKEN_EXPIRED")) { return $true }

  $errs = Get-PropValue -Obj $Body -Name "errors"
  if ($errs -and $errs.Count -gt 0) {
    $err0 = $errs[0]
    $eCode = Get-PropValue -Obj $err0 -Name "code"
    $eDetail = Get-PropValue -Obj $err0 -Name "detail"
    if ($eCode -and ($eCode -match "INVALID_TOKEN|TOKEN_EXPIRED|token_expired|access_token_expired")) { return $true }
    if ($eDetail -and ($eDetail -match "token expired|invalid token|INVALID_TOKEN|TOKEN_EXPIRED")) { return $true }
  }
  return $false
}

function Invoke-SmartsellMultipart {
  param(
    [Parameter(Mandatory=$true)][string]$Method,
    [Parameter(Mandatory=$true)][string]$Url,
    [Parameter(Mandatory=$true)][hashtable]$Form,
    [hashtable]$Headers = $null,
    [int]$TimeoutSec = 30,
    [string]$BaseUrl = $null,
    [string]$AccessToken = $null,
    [string]$RefreshToken = $null,
    [string]$Identifier = $null,
    [string]$Password = $null
  )

  if (-not $BaseUrl) { $BaseUrl = Get-BaseUrlFromUrl -Url $Url }
  if ($AccessToken) { $script:SmartsellAccessToken = Normalize-JwtToken -Value $AccessToken }
  if ($RefreshToken) { $script:SmartsellRefreshToken = Normalize-JwtToken -Value $RefreshToken }

  if (-not $script:SmartsellAccessToken -or -not $script:SmartsellRefreshToken) {
    $cached = Load-SmartsellTokensFromCache -BaseUrl $BaseUrl
    if ($cached) {
      if ($cached.access) { $script:SmartsellAccessToken = $cached.access }
      if ($cached.refresh) { $script:SmartsellRefreshToken = $cached.refresh }
    }
  }

  function Invoke-Once([string]$Token) {
    $reqHeaders = @{}
    if ($Headers) { $reqHeaders = @{} + $Headers }
    if ($Token) { $reqHeaders["Authorization"] = "Bearer $Token" }

    try {
      $resp = Invoke-WebRequestSafe -Params @{
        Method = $Method
        Uri = $Url
        Headers = $reqHeaders
        Form = $Form
        TimeoutSec = $TimeoutSec
      }
    } catch {
      $errMsg = "request failed"
      try { $errMsg = $_.Exception.Message } catch { }
      throw "request failed: status=(no response) body=$errMsg"
    }

    $status = $resp.StatusCode
    $body = $null
    $rid = ""
    if ($resp -and $resp.Headers) {
      $rid = [string](@($resp.Headers["X-Request-ID"])[0])
      if (-not $rid) { $rid = [string](@($resp.Headers["x-request-id"])[0]) }
    }
    if ($resp.Content) {
      try {
        $body = $resp.Content | ConvertFrom-Json
      } catch {
        $body = $resp.Content
      }
    }

    return [PSCustomObject]@{
      StatusCode = $status
      Headers = $resp.Headers
      Body = $body
      RequestId = $rid
      Error = $null
    }
  }

  $resp = Invoke-Once -Token $script:SmartsellAccessToken
  if (-not (Test-AuthExpired -StatusCode $resp.StatusCode -Body $resp.Body)) {
    return $resp
  }

  $refreshed = $null
  if ($script:SmartsellRefreshToken) {
    try {
      $refreshed = Refresh-SmartsellAccessToken -BaseUrl $BaseUrl -RefreshToken $script:SmartsellRefreshToken -TimeoutSec $TimeoutSec
    } catch {
      $refreshed = $null
    }
  }

  if (-not $refreshed -and $Identifier -and $Password) {
    try {
      $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password -TimeoutSec $TimeoutSec
      $script:SmartsellAccessToken = $tokens.access
      $script:SmartsellRefreshToken = $tokens.refresh
    } catch {
      $tokens = $null
    }
  }

  if ($script:SmartsellAccessToken) {
    return Invoke-Once -Token $script:SmartsellAccessToken
  }

  return $resp
}

function Is-DevEnvironment {
  $envName = ($env:ENVIRONMENT ?? "").ToLower()
  $debug = ($env:DEBUG ?? "").ToLower()
  if ($debug -in @("1", "true", "yes", "on")) { return $true }
  return $envName -in @("local", "development", "dev", "test", "testing", "pytest")
}

function Ensure-KaspiOffers {
  param(
    [string]$BaseUrl,
    [string]$MerchantUid,
    [string]$AccessToken = $null,
    [string]$RefreshToken = $null,
    [string]$Identifier = $null,
    [string]$Password = $null,
    [switch]$AllowSeed
  )
  if (-not $MerchantUid) { return $false }

  $listUrl = "$BaseUrl/api/v1/kaspi/offers?merchantUid=$([uri]::EscapeDataString($MerchantUid))&limit=1&offset=0"
  $listResp = Invoke-SmartsellApi -Method "GET" -Url $listUrl -TimeoutSec 20 -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password
  if ($listResp.StatusCode -ge 200 -and $listResp.StatusCode -lt 300) {
    $total = $listResp.Body.total
    if ($total -and [int]$total -gt 0) { return $true }
  }

  $sku = "SMOKE-" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
  $tmpPath = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), "csv")
  $csv = "sku,title,price`n$sku,Smoke Item,1000`n"
  Set-Content -LiteralPath $tmpPath -Value $csv -Encoding UTF8

  $importUrl = "$BaseUrl/api/v1/kaspi/catalog/import?merchantUid=$([uri]::EscapeDataString($MerchantUid))"
  $form = @{ file = Get-Item $tmpPath }
  $importResp = Invoke-SmartsellMultipart -Method "POST" -Url $importUrl -Form $form -TimeoutSec 30 -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password
  Remove-Item -LiteralPath $tmpPath -ErrorAction SilentlyContinue

  if ($importResp.StatusCode -ge 200 -and $importResp.StatusCode -lt 300) {
    return $true
  }

  if ($AllowSeed.IsPresent -and (Is-DevEnvironment)) {
    $seedUrl = "$BaseUrl/api/v1/kaspi/offers/seed"
    $seedBody = @{ merchant_uid = $MerchantUid; sku = $sku; title = "Smoke Item"; price = 1000 }
    $seedResp = Invoke-SmartsellApi -Method "POST" -Url $seedUrl -Body $seedBody -TimeoutSec 20 -AccessToken $AccessToken -RefreshToken $RefreshToken -Identifier $Identifier -Password $Password
    if ($seedResp.StatusCode -ge 200 -and $seedResp.StatusCode -lt 300) {
      return $true
    }
  }

  return $false
}

function Invoke-SmartsellApi {
  param(
    [Parameter(Mandatory=$true)][string]$Method,
    [Parameter(Mandatory=$true)][string]$Url,
    [hashtable]$Headers = $null,
    [object]$Body = $null,
    [int]$TimeoutSec = 20,
    [string]$BaseUrl = $null,
    [string]$AccessToken = $null,
    [string]$RefreshToken = $null,
    [string]$Identifier = $null,
    [string]$Password = $null
  )

  if ($AccessToken) { $script:SmartsellAccessToken = Normalize-JwtToken -Value $AccessToken }
  if ($RefreshToken) { $script:SmartsellRefreshToken = Normalize-JwtToken -Value $RefreshToken }

  if (-not $script:SmartsellAccessToken -and $env:SMARTSELL_ACCESS_TOKEN) {
    $script:SmartsellAccessToken = Normalize-JwtToken -Value $env:SMARTSELL_ACCESS_TOKEN
  }
  if (-not $script:SmartsellAccessToken -and $env:ACCESS_TOKEN) {
    $script:SmartsellAccessToken = Normalize-JwtToken -Value $env:ACCESS_TOKEN
  }
  if (-not $script:SmartsellRefreshToken -and $env:SMARTSELL_REFRESH_TOKEN) {
    $script:SmartsellRefreshToken = Normalize-JwtToken -Value $env:SMARTSELL_REFRESH_TOKEN
  }

  if ($script:SmartsellAccessToken -and -not (Normalize-JwtToken -Value $script:SmartsellAccessToken)) {
    Write-Host ("WARN: access token has invalid format; ignoring token={0}" -f (Mask-Secret $script:SmartsellAccessToken))
    $script:SmartsellAccessToken = $null
  }
  if ($script:SmartsellRefreshToken -and -not (Normalize-JwtToken -Value $script:SmartsellRefreshToken)) {
    Write-Host ("WARN: refresh token has invalid format; ignoring token={0}" -f (Mask-Secret $script:SmartsellRefreshToken))
    $script:SmartsellRefreshToken = $null
  }

  if (-not $BaseUrl) { $BaseUrl = Get-BaseUrlFromUrl -Url $Url }

  if (-not $script:SmartsellAccessToken -or -not $script:SmartsellRefreshToken) {
    $cached = Load-SmartsellTokensFromCache -BaseUrl $BaseUrl
    if ($cached) {
      if ($cached.access) { $script:SmartsellAccessToken = $cached.access }
      if ($cached.refresh) { $script:SmartsellRefreshToken = $cached.refresh }
      Write-Host ("[INFO] Loaded cached tokens from {0}" -f (Get-SmokeCachePath))
    }
  }

  if (-not $Identifier) { $Identifier = $env:ADMIN_IDENTIFIER }
  if (-not $Password) { $Password = $env:ADMIN_PASSWORD }
  if (-not $Identifier) { $Identifier = $env:PLATFORM_IDENTIFIER }
  if (-not $Password) { $Password = $env:PLATFORM_PASSWORD }

  function Invoke-Once([string]$Token) {
    $reqHeaders = @{}
    if ($Headers) { $reqHeaders = @{} + $Headers }
    if ($Token) {
      $tokenValue = ([string]$Token).Trim()
      $reqHeaders["Authorization"] = "Bearer $tokenValue"
    }

    $params = @{
      Method = $Method
      Uri = $Url
      Headers = $reqHeaders
      TimeoutSec = $TimeoutSec
    }

    if ($Body -ne $null) {
      if ($Body -is [string]) {
        $params.Body = $Body
      } else {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
      }
      $params.ContentType = "application/json"
    }

    try {
      $resp = Invoke-WebRequestSafe -Params $params
    } catch {
      $errMsg = "request failed"
      try { $errMsg = $_.Exception.Message } catch { }
      throw "request failed: status=(no response) body=$errMsg"
    }

    $status = $resp.StatusCode
    $body = $null
    $rid = ""
    if ($resp -and $resp.Headers) {
      $rid = [string](@($resp.Headers["X-Request-ID"])[0])
      if (-not $rid) { $rid = [string](@($resp.Headers["x-request-id"])[0]) }
    }
    if ($resp.Content) {
      try {
        $body = $resp.Content | ConvertFrom-Json
      } catch {
        $body = $resp.Content
      }
    }

    return [PSCustomObject]@{
      StatusCode = $status
      Headers = $resp.Headers
      Body = $body
      RequestId = $rid
      Error = $null
    }
  }

  if (-not $BaseUrl) { $BaseUrl = Get-BaseUrlFromUrl -Url $Url }

  $resp = Invoke-Once -Token $script:SmartsellAccessToken
  if (-not (Test-AuthExpired -StatusCode $resp.StatusCode -Body $resp.Body)) {
    return $resp
  }

  $refreshed = $null
  if ($script:SmartsellRefreshToken) {
    try {
      $refreshed = Refresh-SmartsellAccessToken -BaseUrl $BaseUrl -RefreshToken $script:SmartsellRefreshToken -TimeoutSec $TimeoutSec
    } catch {
      $refreshed = $null
    }
  }

  if (-not $refreshed -and $Identifier -and $Password) {
    try {
      $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password -TimeoutSec $TimeoutSec
      $script:SmartsellAccessToken = $tokens.access
      $script:SmartsellRefreshToken = $tokens.refresh
    } catch {
      $tokens = $null
    }
  }

  if ($script:SmartsellAccessToken) {
    return Invoke-Once -Token $script:SmartsellAccessToken
  }

  return $resp
}

function Get-SmokeAccessToken([string]$BaseUrl="http://127.0.0.1:8000") {
  $cache = Join-Path $PSScriptRoot ".smoke-cache.json"
  if (!(Test-Path $cache)) { throw "Нет $cache. Сначала запусти .\scripts\smoke-auth.ps1" }
  $data = Get-Content $cache -Raw | ConvertFrom-Json
  $tok = $data.$BaseUrl.access
  if ([string]::IsNullOrWhiteSpace($tok)) { throw "В кеше нет access для $BaseUrl. Перелогинься: .\scripts\smoke-auth.ps1" }
  return $tok
}
