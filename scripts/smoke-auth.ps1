param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Identifier = "",
  [string]$Password = ""
)

$ErrorActionPreference = "Stop"

function Post-Json($url, $obj) {
  return Invoke-RestMethod -Uri $url -Method POST -TimeoutSec 20 -ContentType "application/json" -Body ($obj | ConvertTo-Json -Depth 10)
}

function Get-Json($url, $headers) {
  return Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 20 -Headers $headers
}

if ([string]::IsNullOrWhiteSpace($Identifier) -or [string]::IsNullOrWhiteSpace($Password)) {
  throw "Pass -Identifier and -Password"
}

$loginUrl   = "$BaseUrl/api/v1/auth/login"
$meUrl      = "$BaseUrl/api/v1/auth/me"
$refreshUrl = "$BaseUrl/api/v1/auth/refresh"
$logoutUrl  = "$BaseUrl/api/v1/auth/logout"

Write-Host "LOGIN  $loginUrl"
$login = Post-Json $loginUrl @{ identifier = $Identifier; password = $Password }

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
Write-Host ("ME OK user_id={0} role={1} company_name={2} phone={3} email={4}" -f $me1.id, $me1.role, $me1.company_name, $me1.phone, $me1.email)

Write-Host "REFRESH $refreshUrl"
$r = Post-Json $refreshUrl @{ refresh_token = $refresh }

$access2 = $r.access_token
if (-not $access2) { $access2 = $r.accessToken }
$refresh2 = $r.refresh_token
if (-not $refresh2) { $refresh2 = $r.refreshToken }

if (-not $access2 -or -not $refresh2) {
  Write-Host ($r | ConvertTo-Json -Depth 10)
  throw "Refresh response missing access_token/refresh_token"
}

$h2 = @{ Authorization = "Bearer $access2" }

Write-Host "ME2    $meUrl"
$me2 = Get-Json $meUrl $h2
Write-Host ("ME2 OK user_id={0} role={1} company_name={2} phone={3} email={4}" -f $me2.id, $me2.role, $me2.company_name, $me2.phone, $me2.email)

Write-Host "LOGOUT $logoutUrl"
try {
  # если logout ожидает refresh_token
  $out = Post-Json $logoutUrl @{ refresh_token = $refresh2 }
  Write-Host "Logout OK"
} catch {
  Write-Host "Logout call failed (maybe different contract)."
  throw
}

Write-Host "REFRESH_AFTER_LOGOUT"
try {
  Post-Json $refreshUrl @{ refresh_token = $refresh2 } | Out-Null
  Write-Host "UNEXPECTED: refresh still works"
} catch {
  Write-Host "OK: refresh blocked"
}

Write-Host "ME_AFTER_LOGOUT"
try {
  $null = Get-Json $meUrl $h2
  Write-Host "ERROR: access token still valid after logout"
  exit 1
} catch {
  Write-Host "OK: /me blocked after logout"
}

Write-Host "DONE OK"

