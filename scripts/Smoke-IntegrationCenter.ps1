<#
.SYNOPSIS
Quick smoke tests for Integration Center admin endpoints.

.PARAMETER Identifier
Platform admin identifier (optional if -Token is provided).

.PARAMETER Password
Platform admin password (optional if -Token is provided).

.ENVIRONMENT
SMARTSELL_PLATFORM_IDENTIFIER / SMARTSELL_PLATFORM_PASSWORD
Fallbacks: PLATFORM_IDENTIFIER / PLATFORM_PASSWORD, ADMIN_IDENTIFIER / ADMIN_PASSWORD

.EXAMPLE
./Smoke-IntegrationCenter.ps1 -BaseUrl "http://127.0.0.1:8000" -Identifier "admin@local" -Password "admin"
./Smoke-IntegrationCenter.ps1 -BaseUrl "http://127.0.0.1:8000" -Token "eyJhbGciOi..."
#>

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Identifier = "",
    [string]$Password = "",
    [string]$Token
)

$ErrorActionPreference = "Stop"

$script:Failures = 0

function Write-Result {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail = ""
    )
    $status = if ($Ok) { "PASS" } else { "FAIL" }
    if (-not $Ok) { $script:Failures++ }
    if ($Detail) {
        Write-Host "$status`t$Name`t$Detail"
    } else {
        Write-Host "$status`t$Name"
    }
}

function Get-TokenFromLogin {
    param(
        [string]$Identifier,
        [string]$Password
    )
    $payload = @{ identifier = $Identifier; password = $Password }
    $resp = Invoke-Api -Method POST -Path "/api/v1/auth/login" -Body $payload -NoAuth
    return $resp.access_token
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        [hashtable]$Body = $null,
        [hashtable]$Query = $null,
        [switch]$NoAuth
    )
    $uriBuilder = New-Object System.UriBuilder("$BaseUrl$Path")
    if ($Query) {
        $queryString = ($Query.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join "&"
        $uriBuilder.Query = $queryString
    }

    $headers = @{ "Content-Type" = "application/json" }
    if (-not $NoAuth -and $Token) { $headers["Authorization"] = "Bearer $Token" }

    $params = @{
        Method = $Method
        Uri    = $uriBuilder.Uri.AbsoluteUri
        Headers = $headers
        ErrorAction = 'Stop'
    }
    if ($Body) { $params["Body"] = ($Body | ConvertTo-Json -Depth 6) }

    return Invoke-RestMethod @params
}

$idSource = "param"
$pwSource = "param"
if ([string]::IsNullOrWhiteSpace($Identifier)) { $Identifier = $env:SMARTSELL_PLATFORM_IDENTIFIER; $idSource = "SMARTSELL_PLATFORM_IDENTIFIER" }
if ([string]::IsNullOrWhiteSpace($Password)) { $Password = $env:SMARTSELL_PLATFORM_PASSWORD; $pwSource = "SMARTSELL_PLATFORM_PASSWORD" }
if ([string]::IsNullOrWhiteSpace($Identifier)) { $Identifier = $env:PLATFORM_IDENTIFIER; $idSource = "PLATFORM_IDENTIFIER (fallback)" }
if ([string]::IsNullOrWhiteSpace($Password)) { $Password = $env:PLATFORM_PASSWORD; $pwSource = "PLATFORM_PASSWORD (fallback)" }
if ([string]::IsNullOrWhiteSpace($Identifier)) { $Identifier = $env:ADMIN_IDENTIFIER; $idSource = "ADMIN_IDENTIFIER (fallback)" }
if ([string]::IsNullOrWhiteSpace($Password)) { $Password = $env:ADMIN_PASSWORD; $pwSource = "ADMIN_PASSWORD (fallback)" }

Write-Host "[INFO] Platform credentials present: ID=$([bool]$Identifier) PW=$([bool]$Password)"
Write-Host "[INFO] Platform credentials source: ID=$idSource PW=$pwSource"

if (-not $Token) {
    if ([string]::IsNullOrWhiteSpace($Identifier) -or [string]::IsNullOrWhiteSpace($Password)) {
        Write-Host "FAIL: SMARTSELL_PLATFORM_IDENTIFIER/SMARTSELL_PLATFORM_PASSWORD (or PLATFORM_/ADMIN_ or -Identifier/-Password) are required when -Token is not provided"
        exit 1
    }
    try {
        $Token = Get-TokenFromLogin -Identifier $Identifier -Password $Password
        Write-Result "platform login" $true
    } catch {
        Write-Result "platform login" $false $_.Exception.Message
        Write-Host "FAIL: cannot continue without platform token"
        exit 1
    }
}

# Providers list checks -------------------------------------------------------
try {
    $domains = @("otp", "payments", "messaging")
    foreach ($d in $domains) {
        $resp = Invoke-Api -Method GET -Path "/api/v1/admin/integrations/providers" -Query @{ domain = $d; limit = 5; offset = 0 }
        $count = ($resp | Measure-Object).Count
        Write-Result "providers/$d" ($count -ge 0) "items=$count"
    }
} catch {
    Write-Result "providers" $false $_.Exception.Message
}

# Events filter check ---------------------------------------------------------
try {
    $evt = Invoke-Api -Method GET -Path "/api/v1/admin/integrations/events" -Query @{ domain = "otp"; provider_to = "noop" }
    $first = $evt | Select-Object -First 1
    $actorEmail = $first.meta_json.actor_email
    Write-Result "events/otp" $true "actor_email=$actorEmail"
} catch {
    Write-Result "events/otp" $false $_.Exception.Message
}

# Config writes (noop + webhook sample) --------------------------------------
try {
    # OTP noop config
    Invoke-Api -Method PUT -Path "/api/v1/admin/integrations/providers/otp/noop/config" -Body @{ config = @{ api_key = "masked"; sender = "+100"; timeout_seconds = 1 } }
    Write-Result "config/otp noop set" $true

    # Messaging webhook config
    Invoke-Api -Method PUT -Path "/api/v1/admin/integrations/messaging/config" -Body @{ provider = "webhook"; config = @{ url = "https://example.invalid/hook"; api_key = "masked"; timeout_s = 2 } }
    Write-Result "config/messaging webhook set" $true
} catch {
    Write-Result "config write" $false $_.Exception.Message
}

# Config redaction checks -----------------------------------------------------
try {
    $cfgOtp = Invoke-Api -Method GET -Path "/api/v1/admin/integrations/providers/otp/noop/config"
    $redacted = $cfgOtp.config.api_key
    Write-Result "config/otp redaction" ($redacted -eq "***") "api_key=$redacted"

    $cfgMsg = Invoke-Api -Method GET -Path "/api/v1/admin/integrations/messaging/config" -Query @{ provider = "webhook" }
    $redactedMsg = $cfgMsg.config.api_key
    Write-Result "config/messaging redaction" ($redactedMsg -eq "***") "api_key=$redactedMsg"
} catch {
    Write-Result "config redaction" $false $_.Exception.Message
}

# Healthchecks ----------------------------------------------------------------
try {
    $hcOtp = Invoke-Api -Method POST -Path "/api/v1/admin/integrations/providers/otp/noop/healthcheck"
    Write-Result "health/otp" $true "status=$($hcOtp.status)"
} catch {
    Write-Result "health/otp" $false $_.Exception.Message
}

try {
    $hcPay = Invoke-Api -Method POST -Path "/api/v1/admin/integrations/providers/payments/noop/healthcheck"
    Write-Result "health/payments" $true "status=$($hcPay.status)"
} catch {
    Write-Result "health/payments" $false $_.Exception.Message
}

try {
    $hcMsg = Invoke-Api -Method GET -Path "/api/v1/admin/integrations/messaging/healthcheck" -Query @{ provider = "webhook" }
    Write-Result "health/messaging" $true "status=$($hcMsg.status)"
} catch {
    Write-Result "health/messaging" $false $_.Exception.Message
}

Write-Host "SUMMARY`tFAILURES=$script:Failures"
if ($script:Failures -gt 0) { exit 1 }
