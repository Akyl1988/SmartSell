<#
Smoke: all reports (CSV + PDF)

Usage:
  pwsh -NoProfile -File .\scripts\smoke-reports-all.ps1 -BaseUrl http://127.0.0.1:8000 -Limit 5

Env:
  SMARTSELL_BASE_URL, LIMIT
#>

param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [int]$Limit = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ScriptDir {
  if ($PSScriptRoot) { return $PSScriptRoot }
  if ($MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    return Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  return (Get-Location).Path
}

function Step([string]$msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:8000" }

$scriptDir = Get-ScriptDir

$steps = @(
  @{ Name = "wallet transactions csv"; Script = "smoke-reports-wallet-transactions.ps1"; Args = @("-BaseUrl", $BaseUrl, "-Limit", $Limit) },
  @{ Name = "orders csv"; Script = "smoke-reports-orders.ps1"; Args = @("-BaseUrl", $BaseUrl, "-Limit", $Limit) },
  @{ Name = "order items csv"; Script = "smoke-reports-order-items.ps1"; Args = @("-BaseUrl", $BaseUrl, "-Limit", $Limit) },
  @{ Name = "orders pdf"; Script = "smoke-reports-orders-pdf.ps1"; Args = @("-BaseUrl", $BaseUrl, "-Limit", $Limit) },
  @{ Name = "sales pdf"; Script = "smoke-reports-sales-pdf.ps1"; Args = @("-BaseUrl", $BaseUrl, "-Limit", $Limit) }
)

foreach ($step in $steps) {
  Step "Running $($step.Name)"
  $path = Join-Path $scriptDir $step.Script
  & pwsh -NoProfile -File $path @($step.Args)
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
