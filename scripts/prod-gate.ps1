<#
Release gate: standardized checks before MVP release.

Usage:
  pwsh -NoProfile -File .\scripts\prod-gate.ps1 -BaseUrl http://127.0.0.1:8000
  pwsh -NoProfile -File .\scripts\prod-gate.ps1 -SkipSmoke
  pwsh -NoProfile -File .\scripts\prod-gate.ps1 -SkipFormatCheck
#>

param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [switch]$SkipSmoke,
  [switch]$SkipFormatCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section([string]$Title) {
  Write-Host "`n=== $Title ===" -ForegroundColor Cyan
}

function Run-Step([string]$Title, [scriptblock]$Action) {
  Write-Section $Title
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $global:LASTEXITCODE = 0
  & $Action
  $sw.Stop()
  if ($LASTEXITCODE -ne 0) {
    throw "$Title failed (exit code $LASTEXITCODE)"
  }
  Write-Host ("{0} done in {1:n2}s" -f $Title, $sw.Elapsed.TotalSeconds)
}

$overall = [System.Diagnostics.Stopwatch]::StartNew()
try {
  if ($SkipFormatCheck.IsPresent) {
    Write-Section "ruff format --check (skip)"
  } else {
    Run-Step "ruff format --check" {
      python -m ruff format --check .
    }
  }

  Run-Step "ruff check" {
    python -m ruff check .
  }

  Run-Step "pytest" {
    python -m pytest -q
  }

  Run-Step "alembic sanity" {
    alembic heads
    alembic history
  }

  if ($SkipSmoke.IsPresent) {
    Write-Section "smoke reports all (skip)"
  } else {
    Run-Step "smoke reports all" {
      pwsh -NoProfile -File .\scripts\smoke-reports-all.ps1 -BaseUrl $BaseUrl -Limit 5
    }
  }

  $overall.Stop()
  Write-Host ("`nSUCCESS in {0:n2}s" -f $overall.Elapsed.TotalSeconds) -ForegroundColor Green
} catch {
  $overall.Stop()
  Write-Host ("`nFAIL in {0:n2}s" -f $overall.Elapsed.TotalSeconds) -ForegroundColor Red
  Write-Host $_
  exit 1
}
