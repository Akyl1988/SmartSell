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

  if (-not [string]::IsNullOrWhiteSpace($Identifier) -and -not [string]::IsNullOrWhiteSpace($Password)) {
    Run-Step "AUTH SMOKE" {
      & "$PSScriptRoot\smoke-auth.ps1" -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password
    }
  } else {
    Write-Host "[WARN] AUTH SMOKE skipped (Identifier/Password not provided)"
  }

  Write-Host "[OK] DONE"
} catch {
  Write-Host "[FAIL] $($_)"
  exit 1
}
