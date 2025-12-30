param(
    [string]$RuntimeUrl = $env:DATABASE_URL,
    [string]$TestUrl    = $env:TEST_DATABASE_URL
)

$ErrorActionPreference = "Stop"

# Defaults if env vars are not set
if (-not $RuntimeUrl) {
    $RuntimeUrl = "postgresql://smartsell@127.0.0.1:5432/smartsell_main"
}
if (-not $TestUrl) {
    $TestUrl = "postgresql://smartsell@127.0.0.1:5432/smartsell_test"
}

$root = (Get-Location)
$toolsDir = Join-Path $root "tools"
if (-not (Test-Path $toolsDir)) { New-Item -ItemType Directory -Path $toolsDir | Out-Null }

$sqlPath = Join-Path $toolsDir "utf8_probe.sql"
$sql = @"
SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS public._utf8_probe (
  id  serial PRIMARY KEY,
  txt text NOT NULL
);

TRUNCATE public._utf8_probe;

INSERT INTO public._utf8_probe(txt) VALUES
  ('Қазақша тест'),
  ('русский текст'),
  ('emoji ✅🔥');

SELECT id, txt FROM public._utf8_probe ORDER BY id;
"@
[System.IO.File]::WriteAllText($sqlPath, $sql, [System.Text.UTF8Encoding]::new($false))

function Invoke-Probe {
    param([string]$Url, [string]$Label)
    Write-Host "`n=== UTF8 PROBE on $Label ===" -ForegroundColor Cyan
    & psql -d $Url -X -v ON_ERROR_STOP=1 -f $sqlPath
    if ($LASTEXITCODE -ne 0) {
        throw "UTF8 probe failed for $Label with exit code $LASTEXITCODE"
    }
}

$env:PGCLIENTENCODING = "UTF8"
Invoke-Probe -Url $RuntimeUrl -Label "runtime"
Invoke-Probe -Url $TestUrl -Label "test"
