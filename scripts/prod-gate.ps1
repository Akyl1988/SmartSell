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

try {
  Run-Step "RUFF" {
    python -m ruff check .
  }

  Run-Step "PYTEST" {
    python -m pytest -q
  }

  _Require-Creds

  Run-Step "LOGIN CHECK" {
    $loginUrl = "$BaseUrl/api/v1/auth/login"
    $payload = @{ identifier = $Identifier; password = $Password }
    $resp = Invoke-WebRequest -Uri $loginUrl -Method POST -TimeoutSec 20 -ContentType "application/json" -Body ($payload | ConvertTo-Json -Depth 10) -SkipHttpErrorCheck
    if ($resp.StatusCode -lt 200 -or $resp.StatusCode -ge 300) {
      if ($resp.Content) {
        Write-Host $resp.Content
      }
      exit 1
    }
  }

  Run-Step "SMOKE CORE" {
    pwsh -NoProfile -File .\scripts\smoke-core.ps1 -BaseUrl $BaseUrl -Identifier $Identifier -Password $Password
  }

  Write-Host "DONE OK"
} catch {
  Write-Host $_
  exit 1
}
