param(
  [string]$BaseUrl = $env:SMARTSELL_BASE_URL,
  [string]$Identifier = $env:ADMIN_IDENTIFIER,
  [string]$Password = $env:ADMIN_PASSWORD,
  [string]$Phone = "",
  [string]$CompanyName = "Demo Company",
  [switch]$RegisterIfMissing
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

$loginUrl = "$BaseUrl/api/v1/auth/login"
$registerUrl = "$BaseUrl/api/v1/auth/register"

Write-Host "LOGIN  $loginUrl (ID source=$idSource PW source=$pwSource)"
try {
  $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password
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

  Invoke-RestMethod -Method POST -Uri $registerUrl -TimeoutSec 20 -ContentType "application/json" -Body ($payload | ConvertTo-Json)
  $tokens = Get-SmartsellTokens -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password
}

$access = $tokens.access
$refresh = $tokens.refresh
Set-SmartsellTokens -AccessToken $access -RefreshToken $refresh -BaseUrl $BaseUrl

Write-Host ("ACCESS: {0}" -f (Mask-Secret $access))
Write-Host ("REFRESH: {0}" -f (Mask-Secret $refresh))
Write-Host ("CACHE: {0}" -f (Get-SmokeCachePath))

$meResp = Invoke-SmartsellApi -Method "GET" -Url "$BaseUrl/api/v1/auth/me" -TimeoutSec 20 -AccessToken $access -RefreshToken $refresh -Identifier $Identifier -Password $Password
$me1 = $meResp.Body

$meCompanyId = Resolve-ProfileValue -Profile $me1 -Name "company_id"
$meCompanyName = Resolve-ProfileValue -Profile $me1 -Name "company_name"
$meKaspiStore = Resolve-ProfileValue -Profile $me1 -Name "kaspi_store_id"
$meUserId = Resolve-ProfileValue -Profile $me1 -Name "id"
$meRole = Resolve-ProfileValue -Profile $me1 -Name "role"
Write-Host ("ME OK user_id={0} role={1} company_id={2} company_name={3} kaspi_store_id={4}" -f $meUserId, $meRole, $meCompanyId, $meCompanyName, $meKaspiStore)

$result = @{
  access_token = (Mask-Secret $access)
  refresh_token = (Mask-Secret $refresh)
  user = $me1
}
Write-Output ($result | ConvertTo-Json -Depth 10)

