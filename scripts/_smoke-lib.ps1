Set-StrictMode -Version Latest
$script:SmartsellAccessToken = $null
$script:SmartsellRefreshToken = $null

function Mask-Secret([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
  if ($Value.Length -le 8) { return ("*" * $Value.Length) }
  return ($Value.Substring(0,4) + "..." + $Value.Substring($Value.Length-4))
}

function Test-AsciiValue {
  param([string]$Value)
  if ($null -eq $Value) { return $false }
  return -not ([string]$Value -match "[^\x20-\x7E]")
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
    [string]$RefreshToken
  )
  if ($AccessToken) {
    $AccessToken = ([string]$AccessToken).Trim()
    if (-not (Test-AsciiValue -Value $AccessToken)) {
      $AccessToken = ""
    }
    $script:SmartsellAccessToken = $AccessToken
    $env:SMARTSELL_ACCESS_TOKEN = $AccessToken
  }
  if ($RefreshToken) {
    $RefreshToken = ([string]$RefreshToken).Trim()
    if (-not (Test-AsciiValue -Value $RefreshToken)) {
      $RefreshToken = ""
    }
    $script:SmartsellRefreshToken = $RefreshToken
    $env:SMARTSELL_REFRESH_TOKEN = $RefreshToken
  }
}

function Get-SmartsellTokens {
  param(
    [string]$BaseUrl,
    [string]$Identifier,
    [string]$Password,
    [int]$TimeoutSec = 20
  )
  $loginUrl = "$BaseUrl/api/v1/auth/login"
  $login = Invoke-RestMethod -Method POST -Uri $loginUrl -TimeoutSec $TimeoutSec -ContentType "application/json" -Body (@{ identifier = $Identifier; password = $Password } | ConvertTo-Json)

  $access = $login.access_token
  if (-not $access) { $access = $login.accessToken }
  $refresh = $login.refresh_token
  if (-not $refresh) { $refresh = $login.refreshToken }

  if (-not $access -or -not $refresh) {
    throw "Login response missing access_token/refresh_token"
  }

  Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh

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
  $refreshUrl = "$BaseUrl/api/v1/auth/refresh"
  $body = @{ refresh_token = $RefreshToken } | ConvertTo-Json
  $data = Invoke-RestMethod -Method POST -Uri $refreshUrl -TimeoutSec $TimeoutSec -ContentType "application/json" -Body $body

  $access = $data.access_token
  if (-not $access) { $access = $data.accessToken }
  $refresh = $data.refresh_token
  if (-not $refresh) { $refresh = $data.refreshToken }

  if (-not $access) { return $null }
  Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh
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

  if ($AccessToken) { $script:SmartsellAccessToken = $AccessToken }
  if ($RefreshToken) { $script:SmartsellRefreshToken = $RefreshToken }

  if (-not $script:SmartsellAccessToken -and $env:SMARTSELL_ACCESS_TOKEN) {
    $script:SmartsellAccessToken = $env:SMARTSELL_ACCESS_TOKEN
  }
  if (-not $script:SmartsellRefreshToken -and $env:SMARTSELL_REFRESH_TOKEN) {
    $script:SmartsellRefreshToken = $env:SMARTSELL_REFRESH_TOKEN
  }

  if ($script:SmartsellAccessToken -and -not (Test-AsciiValue -Value $script:SmartsellAccessToken)) {
    Write-Host ("WARN: access token contains non-ASCII chars; ignoring token={0}" -f (Mask-Secret $script:SmartsellAccessToken))
    $script:SmartsellAccessToken = $null
  }
  if ($script:SmartsellRefreshToken -and -not (Test-AsciiValue -Value $script:SmartsellRefreshToken)) {
    Write-Host ("WARN: refresh token contains non-ASCII chars; ignoring token={0}" -f (Mask-Secret $script:SmartsellRefreshToken))
    $script:SmartsellRefreshToken = $null
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
      return [PSCustomObject]@{
        StatusCode = 0
        Headers = $null
        Body = $null
        RequestId = ""
        Error = $errMsg
      }
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
