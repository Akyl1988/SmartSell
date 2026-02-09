param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Identifier = $env:ADMIN_IDENTIFIER,
  [string]$Password = $env:ADMIN_PASSWORD,
  [string]$Phone = "",
  [string]$CompanyName = "Demo Company",
  [switch]$RegisterIfMissing
)

$ErrorActionPreference = "Stop"

function Post-Json($url, $obj, $headers = $null) {
  $params = @{
    Uri = $url
    Method = "POST"
    TimeoutSec = 20
    ContentType = "application/json"
    Body = ($obj | ConvertTo-Json -Depth 10)
  }
  if ($headers) { $params.Headers = $headers }
  return Invoke-RestMethod @params
}

function Mask-Secret([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
  if ($Value.Length -le 8) { return ("*" * $Value.Length) }
  return ($Value.Substring(0,4) + "..." + $Value.Substring($Value.Length-4))
}

function Get-Json($url, $headers) {
  return Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 20 -Headers $headers
}

$identifierProvided = $PSBoundParameters.ContainsKey("Identifier")
$passwordProvided = $PSBoundParameters.ContainsKey("Password")

if (-not $Identifier) { $Identifier = $env:PLATFORM_IDENTIFIER }
if (-not $Password) { $Password = $env:PLATFORM_PASSWORD }

$idSource = if ($identifierProvided) { "param:Identifier" } elseif ($env:ADMIN_IDENTIFIER) { "ADMIN_IDENTIFIER" } else { "PLATFORM_IDENTIFIER" }
$pwSource = if ($passwordProvided) { "param:Password" } elseif ($env:ADMIN_PASSWORD) { "ADMIN_PASSWORD" } else { "PLATFORM_PASSWORD" }

if ([string]::IsNullOrWhiteSpace($Identifier) -or [string]::IsNullOrWhiteSpace($Password)) {
  Write-Host "Missing credentials. Example:"
  Write-Host "  pwsh -NoProfile -File .\scripts\smoke-auth.ps1 -BaseUrl http://127.0.0.1:8000 -Identifier admin@local -Password 'admin'"
  throw "Pass -Identifier and -Password"
}

$loginUrl   = "$BaseUrl/api/v1/auth/login"
$registerUrl = "$BaseUrl/api/v1/auth/register"
$meUrl      = "$BaseUrl/api/v1/auth/me"

Write-Host "LOGIN  $loginUrl (ID source=$idSource PW source=$pwSource)"
try {
  $login = Post-Json $loginUrl @{ identifier = $Identifier; password = $Password }
} catch {
  if (-not $RegisterIfMissing) { throw }
  Write-Host "LOGIN failed. Attempting register: $registerUrl"

  $payload = @{ password = $Password; company_name = $CompanyName }
  if ($Identifier -match "@") {
    $payload.email = $Identifier
    if (-not $Phone) { throw "Register requires -Phone when using email identifier." }
    $payload.phone = $Phone
  } else {
    $payload.phone = $Identifier
  }

  $register = Post-Json $registerUrl $payload
  $login = $register
}

# Поддержим разные форматы ответа (на всякий случай)
$access = $login.access_token
if (-not $access) { $access = $login.accessToken }
$refresh = $login.refresh_token
if (-not $refresh) { $refresh = $login.refreshToken }

if (-not $access -or -not $refresh) {
  Write-Host ($login | ConvertTo-Json -Depth 10)
  throw "Login response missing access_token/refresh_token"
}

$h = @{ Authorization = "Bearer $access" }

Write-Host "ME     $meUrl"
$me1 = Get-Json $meUrl $h
Write-Host ("ME OK user_id={0} role={1} company_id={2} company_name={3} kaspi_store_id={4}" -f $me1.id, $me1.role, $me1.company_id, $me1.company_name, $me1.kaspi_store_id)

Write-Host ("TOKEN: {0}" -f (Mask-Secret $access))

$result = @{
  token = $access
  refresh_token = $refresh
  user = $me1
}
Write-Output ($result | ConvertTo-Json -Depth 10)

