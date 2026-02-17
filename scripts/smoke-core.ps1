<#
Smoke core (OpenAPI + health + optional auth)
Params:
  -BaseUrl http://127.0.0.1:8000
  -Identifier / -Password (optional; if missing => WARN, exit 0)
Env examples:
  SMARTSELL_IDENTIFIER / SMARTSELL_PASSWORD (pass via -Identifier/-Password)
#>

param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Identifier = "",
  [string]$Password = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_smoke-lib.ps1"

function Run-Step([string]$title, [scriptblock]$action) {
  Write-Host "[INFO] $title"
  & $action
}

try {
  Run-Step "OPENAPI SMOKE" {
    & "$PSScriptRoot\smoke-openapi.ps1" -BaseUrl $BaseUrl
  }

  Run-Step "WALLET HEALTH $BaseUrl/api/v1/wallet/health" {
    $resp = Invoke-WebRequest -Uri "$BaseUrl/api/v1/wallet/health" -Method GET -TimeoutSec 20
    if ($resp.StatusCode -ne 200) {
      throw "Wallet health status $($resp.StatusCode)"
    }
    Write-Host "[OK] wallet health $($resp.StatusCode)"
  }

  $resolvedIdentifier = $Identifier
  if ([string]::IsNullOrWhiteSpace($resolvedIdentifier)) { $resolvedIdentifier = $env:SMARTSELL_IDENTIFIER }
  $resolvedPassword = $Password
  if ([string]::IsNullOrWhiteSpace($resolvedPassword)) { $resolvedPassword = $env:SMARTSELL_PASSWORD }

  $envAccess = $null
  if ($env:SMARTSELL_ACCESS_TOKEN) { $envAccess = Normalize-JwtToken -Value $env:SMARTSELL_ACCESS_TOKEN }
  if (-not $envAccess -and $env:ACCESS_TOKEN) { $envAccess = Normalize-JwtToken -Value $env:ACCESS_TOKEN }
  $envRefresh = $null
  if ($env:SMARTSELL_REFRESH_TOKEN) { $envRefresh = Normalize-JwtToken -Value $env:SMARTSELL_REFRESH_TOKEN }
  $cacheTokens = Load-SmartsellTokensFromCache -BaseUrl $BaseUrl

  $hasTokens = $envAccess -or $envRefresh -or ($cacheTokens -and ($cacheTokens.access -or $cacheTokens.refresh))
  $hasCredentials = -not [string]::IsNullOrWhiteSpace($resolvedIdentifier) -and -not [string]::IsNullOrWhiteSpace($resolvedPassword)

  if ($hasTokens -or $hasCredentials) {
    Run-Step "AUTH SMOKE" {
      $headers = Ensure-SmartsellAuth -BaseUrl $BaseUrl -Identifier $resolvedIdentifier -Password $resolvedPassword
      $authHeader = $headers.Authorization
      $resp = Invoke-WebRequestSafe -Params @{
        Method = "GET"
        Uri = "$BaseUrl/api/v1/auth/me"
        Headers = @{ Authorization = $authHeader }
        TimeoutSec = 20
      }
      $profile = $null
      if ($resp.Content) {
        try { $profile = $resp.Content | ConvertFrom-Json } catch { $profile = $null }
      }
      $userId = $null
      if ($profile) { $userId = Resolve-ProfileValue -Profile $profile -Name "user_id" }
      if (-not $userId -and $profile) { $userId = Resolve-ProfileValue -Profile $profile -Name "id" }
      $role = $null
      if ($profile) { $role = Resolve-ProfileValue -Profile $profile -Name "role" }
      $companyId = $null
      if ($profile) { $companyId = Resolve-ProfileValue -Profile $profile -Name "company_id" }
      if (-not $companyId -and $profile) { $companyId = Resolve-ProfileValue -Profile $profile -Name "companyId" }
      Write-Host "[OK] AUTH me OK user_id=$userId role=$role company_id=$companyId"
    }
  } else {
    Write-Host "[WARN] AUTH SMOKE skipped (set SMARTSELL_ACCESS_TOKEN or ACCESS_TOKEN, SMARTSELL_REFRESH_TOKEN, or SMARTSELL_IDENTIFIER/SMARTSELL_PASSWORD; cache: scripts/.smoke-cache.json)"
  }

  Write-Host "[OK] DONE"
} catch {
  Write-Host "[FAIL] $($_)"
  exit 1
}
