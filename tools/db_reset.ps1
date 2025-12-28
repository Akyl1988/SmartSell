param(
    [string]$RuntimeUrl = $env:DATABASE_URL,
    [string]$TestUrl    = $env:TEST_DATABASE_URL,
    [string]$AlembicBin = "alembic",
    [switch]$AllowStamp,
    [switch]$RunUtf8Probe,
    [switch]$UseIcu,
    [string]$IcuLocale = "kk-KZ-x-icu",
    [switch]$IcuStrict
)

$ErrorActionPreference = "Stop"
function Write-Info($msg) { Write-Host "[info] $msg" }
function Write-Warn($msg) { Write-Warning $msg }

$timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$root = (Split-Path -Parent $PSScriptRoot)
$docsDir = Join-Path $root "docs"
if (-not (Test-Path $docsDir)) { New-Item -ItemType Directory -Path $docsDir | Out-Null }
$reportPath = Join-Path $docsDir "DB_SETUP_${timestamp}.md"

$report = @()
$hadError = $false
$exitCode = 1
$failureMessages = @()
$report += "# SmartSell DB Setup ($timestamp)"
$report += "Env: PGHOST=$($env:PGHOST) PGPORT=$($env:PGPORT)"
$report += ""

if (-not $RuntimeUrl) { throw "DATABASE_URL is required (smartsell_main)." }
if (-not $TestUrl) { throw "TEST_DATABASE_URL is required (smartsell_test)." }

function Invoke-PsqlLine {
    param([string]$Url, [string]$Sql)
    $args = @('-d', $Url, '-X', '-q', '-t', '-w', '-P', 'pager=off', '-c', $Sql)
    & psql @args 2>&1
}

function Get-ServerVersionNum {
    param([string]$AdminUrl)
    $v = Invoke-PsqlLine -Url $AdminUrl -Sql "show server_version_num;" | ForEach-Object { $_.Trim() } | Select-Object -First 1
    try { return [int]$v } catch { return 0 }
}

function Parse-PostgresUrl {
    param([string]$Url)
    $uri = [uri]$Url
    $dbName = $uri.AbsolutePath.Trim('/')
    $pgHost = $uri.Host
    $port = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
    $userInfo = $uri.UserInfo
    $owner = if ($userInfo) { ($userInfo.Split(':')[0]) } else { '' }
    $scheme = $uri.Scheme
    $adminUrl = "${scheme}://"
    if ($userInfo) { $adminUrl += "$userInfo@" }
    $adminUrl += "${pgHost}:${port}/postgres"
    return @{ DbName = $dbName; AdminUrl = $adminUrl; Owner = $owner }
}

function Recreate-DatabaseWithOptionalIcu {
    param(
        [string]$Url,
        [string]$Label,
        [bool]$EnableIcu,
        [int]$ServerVersion
    )

    $parts = Parse-PostgresUrl -Url $Url
    $dbName = $parts.DbName
    $adminUrl = $parts.AdminUrl
    $owner = if ($parts.Owner) { $parts.Owner } else { 'smartsell' }

    Write-Info "Recreating database '$dbName' ($Label)"

    $dropSql = @"
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$dbName' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "$dbName";
"@
    Invoke-PsqlLine -Url $adminUrl -Sql $dropSql | Out-Null

    $createdWithIcu = $false
    if ($EnableIcu) {
        $icuSql = @"
CREATE DATABASE "$dbName"
  WITH OWNER "$owner"
       TEMPLATE template0
       ENCODING 'UTF8'
       LOCALE_PROVIDER icu
       ICU_LOCALE '$IcuLocale';
"@
        try {
            Invoke-PsqlLine -Url $adminUrl -Sql $icuSql | Out-Null
            $verifySql = "select datlocprovider, datlocale from pg_database where datname='$dbName';"
            $verify = Invoke-PsqlLine -Url $adminUrl -Sql $verifySql | ForEach-Object { $_.Trim() }
            $provider = $null; $locale = $null
            if ($verify) {
                $line = $verify | Select-Object -First 1
                $parts = $line -split '\s*\|\s*'
                if ($parts.Count -ge 2) {
                    $provider = $parts[0].Trim(); $locale = $parts[1].Trim()
                } else {
                    $parts = $line -split '\s+'
                    if ($parts.Count -ge 2) { $provider = $parts[0].Trim(); $locale = $parts[1].Trim() }
                }
            }

            $localeMatch = $false
            if ($locale) { $localeMatch = [string]::Equals($locale, $IcuLocale, [System.StringComparison]::InvariantCultureIgnoreCase) }

            if ($provider -eq 'i' -and $localeMatch) {
                $createdWithIcu = $true
                Write-Info "Created $dbName with ICU locale $IcuLocale"
            } else {
                if ($IcuStrict) {
                    $script:exitCode = 3
                    throw "ICU verification failed for $dbName ($Label): provider='$provider', locale='$locale'"
                }
                Write-Warn "ICU verification failed for $dbName ($Label): provider='$provider', locale='$locale'. Falling back to default locale."
                Invoke-PsqlLine -Url $adminUrl -Sql $dropSql | Out-Null
            }
        } catch {
            if ($IcuStrict) {
                $script:exitCode = 3
                throw "ICU creation failed for $dbName ($Label): $($_.Exception.Message)"
            }
            Write-Warn "ICU creation failed for $dbName ($Label): $($_.Exception.Message). Falling back to default locale."
        }
    }

    if (-not $createdWithIcu) {
        $fallbackSql = @"
CREATE DATABASE "$dbName"
  WITH OWNER "$owner"
       TEMPLATE template0
       ENCODING 'UTF8';
"@
        Invoke-PsqlLine -Url $adminUrl -Sql $fallbackSql | Out-Null
        Write-Warn "Created $dbName WITHOUT ICU (fallback)"
    }
}

function Section {
    param([string]$Title, [string]$Content)
    $script:report += "## $Title"
    $script:report += '```'
    $script:report += $Content
    $script:report += '```'
}

function Mask-UrlPassword {
    param([string]$Url)
    if (-not $Url) { return $Url }
    if ($Url -match "^(?<scheme>[^:]+://)(?<user>[^:@/]+)?(?::(?<pass>[^@]*))?@(?<rest>.+)$") {
        $scheme = $matches['scheme']
        $user = $matches['user']
        $rest = $matches['rest']
        return "$scheme$user@$rest"
    }
    return $Url
}

function Run-AlembicUpgrade {
    param([string]$Url, [bool]$IsTest)
    $envBackup = @{}
    foreach ($k in 'DATABASE_URL','TEST_DATABASE_URL','TEST_ASYNC_DATABASE_URL','TESTING','PYTHONPATH') {
        $envBackup[$k] = Get-ChildItem Env:$k -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue
    }

    try {
        if ($IsTest) {
            Write-Info "Running alembic upgrade head (TESTING=1)"
            Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
            $env:TESTING = "1"
            $env:TEST_DATABASE_URL = $Url
            Remove-Item Env:TEST_ASYNC_DATABASE_URL -ErrorAction SilentlyContinue
        } else {
            Write-Info "Running alembic upgrade head (runtime)"
            $env:DATABASE_URL = $Url
            Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:TEST_ASYNC_DATABASE_URL -ErrorAction SilentlyContinue
            $env:TESTING = "0"
        }
        $alembicOutput = & $AlembicBin upgrade head 2>&1
        return $alembicOutput
    } finally {
        foreach ($pair in $envBackup.GetEnumerator()) {
            if ($null -ne $pair.Value -and $pair.Value -ne "") {
                Set-Item -Path "Env:$($pair.Key)" -Value $pair.Value -ErrorAction SilentlyContinue
            } else {
                Remove-Item Env:$($pair.Key) -ErrorAction SilentlyContinue
            }
        }
    }
}

function Run-AlembicStampHead {
    param([string]$Url, [bool]$IsTest)
    $envBackup = @{}
    foreach ($k in 'DATABASE_URL','TEST_DATABASE_URL','TEST_ASYNC_DATABASE_URL','TESTING','PYTHONPATH') {
        $envBackup[$k] = Get-ChildItem Env:$k -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue
    }
    try {
        if ($IsTest) {
            Write-Warn "[ALLOW_STAMP] Stamping head for TEST database"
            Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
            $env:TESTING = "1"
            $env:TEST_DATABASE_URL = $Url
            Remove-Item Env:TEST_ASYNC_DATABASE_URL -ErrorAction SilentlyContinue
        } else {
            Write-Warn "[ALLOW_STAMP] Stamping head for RUNTIME database"
            $env:DATABASE_URL = $Url
            Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:TEST_ASYNC_DATABASE_URL -ErrorAction SilentlyContinue
            $env:TESTING = "0"
        }
        $output = & $AlembicBin stamp head 2>&1
        return $output
    } finally {
        foreach ($pair in $envBackup.GetEnumerator()) {
            if ($null -ne $pair.Value -and $pair.Value -ne "") {
                Set-Item -Path "Env:$($pair.Key)" -Value $pair.Value -ErrorAction SilentlyContinue
            } else {
                Remove-Item Env:$($pair.Key) -ErrorAction SilentlyContinue
            }
        }
    }
}

function Ensure-AlembicVersionSize {
    param([string]$Url)
    $sql = @"
DO $$
BEGIN
    -- Create alembic_version if missing, with 256-char column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'
    ) THEN
        EXECUTE 'CREATE TABLE public.alembic_version (version_num VARCHAR(256) NOT NULL)';
    END IF;

    -- Widen existing alembic_version.version_num if it is too short
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public'
           AND table_name='alembic_version'
           AND column_name='version_num'
           AND (character_maximum_length < 256 OR character_maximum_length IS NULL)
    ) THEN
        EXECUTE 'ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE VARCHAR(256)';
    END IF;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;
"@
    Invoke-PsqlLine -Url $Url -Sql $sql | Out-Null
}

function Check-Tables {
    param([string]$Url, [string]$Label)
    $countSql = "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';"
    $count = Invoke-PsqlLine -Url $Url -Sql $countSql | ForEach-Object { $_.Trim() } | Select-Object -First 1

    $keysSql = @"
WITH required(name) AS (
  VALUES ('users'), ('companies'), ('subscriptions')
)
SELECT name FROM required r
WHERE NOT EXISTS (
  SELECT 1 FROM information_schema.tables t
   WHERE t.table_schema='public' AND t.table_name=r.name
);
"@
        $missing = Invoke-PsqlLine -Url $Url -Sql $keysSql | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        if (-not $missing) { $missing = @() }

    $countVal = 0
    if ($count) { $countVal = [int]($count -as [int]) }

    $result = @{
        Count = $countVal
        Missing = $missing
    }

    $details = "tables_in_public=$($result.Count); missing=[$( ($result.Missing -join ', ') )]"

    if ($result.Count -lt 10 -or ($result.Missing.Count -gt 0)) {
        $msg = "FAILED integrity check for ${Label}: $details"
        Section -Title "Post-upgrade validation ($Label)" -Content $msg
        throw $msg
    }

    Section -Title "Post-upgrade validation ($Label)" -Content "OK: $details"
    return $result
}

function Run-AlembicCurrent {
    param([string]$Url, [bool]$IsTest)
    $envBackup = @{}
    foreach ($k in 'DATABASE_URL','TEST_DATABASE_URL','TEST_ASYNC_DATABASE_URL','TESTING','PYTHONPATH') {
        $envBackup[$k] = Get-ChildItem Env:$k -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue
    }
    try {
        if ($IsTest) {
            $env:TESTING = "1"
            Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
            $env:TEST_DATABASE_URL = $Url
            Remove-Item Env:TEST_ASYNC_DATABASE_URL -ErrorAction SilentlyContinue
        } else {
            $env:TESTING = "0"
            $env:DATABASE_URL = $Url
            Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:TEST_ASYNC_DATABASE_URL -ErrorAction SilentlyContinue
        }
        $output = & $AlembicBin current -v 2>&1
        return $output
    } finally {
        foreach ($pair in $envBackup.GetEnumerator()) {
            if ($null -ne $pair.Value -and $pair.Value -ne "") {
                Set-Item -Path "Env:$($pair.Key)" -Value $pair.Value -ErrorAction SilentlyContinue
            } else {
                Remove-Item Env:$($pair.Key) -ErrorAction SilentlyContinue
            }
        }
    }
}

try {
    # defaults for summary even if failure happens early
    $runtimeValidation = @{ Count = 'n/a'; Missing = @() }
    $testValidation = @{ Count = 'n/a'; Missing = @() }
    $currentRuntime = ''
    $currentTest = ''
    $utf8ProbeOutput = ''

    $runtimeParts = Parse-PostgresUrl -Url $RuntimeUrl
    $testParts = Parse-PostgresUrl -Url $TestUrl

    if ($runtimeParts.DbName -eq $testParts.DbName) {
        throw "Runtime and test database names must differ."
    }

    $serverVersion = Get-ServerVersionNum -AdminUrl $runtimeParts.AdminUrl
    $icuSupported = $UseIcu -and ($serverVersion -ge 150000)
    if ($UseIcu -and -not $icuSupported) {
        if ($IcuStrict) {
            $script:exitCode = 2
            throw "ICU per-database is not supported on this PostgreSQL version ($serverVersion)."
        }
        Write-Warn "ICU per-database is not supported on PostgreSQL version $serverVersion; falling back to default locale."
    }

    # 0) Optional ICU recreation
    if ($UseIcu) {
        Recreate-DatabaseWithOptionalIcu -Url $RuntimeUrl -Label "runtime" -EnableIcu:$icuSupported -ServerVersion $serverVersion
        Recreate-DatabaseWithOptionalIcu -Url $TestUrl -Label "test" -EnableIcu:$icuSupported -ServerVersion $serverVersion

        $icuCheckSql = @"
select datname,
       pg_encoding_to_char(encoding) as encoding,
       datcollate,
       datctype,
       datlocale,
       datlocprovider
from pg_database
where datname in ('smartsell_main','smartsell_test')
order by datname;
"@
        $icuCheck = Invoke-PsqlLine -Url $RuntimeUrl -Sql $icuCheckSql
        Section -Title "ICU database settings" -Content $icuCheck
    }

    # 1) Connectivity checks
    Write-Info "Checking runtime DB connectivity"
    $runtimeConn = Invoke-PsqlLine -Url $RuntimeUrl -Sql "select current_database(), current_user, now()"
    Section -Title "Runtime connectivity (smartsell_main)" -Content $runtimeConn

    Write-Info "Checking test DB connectivity"
    $testConn = Invoke-PsqlLine -Url $TestUrl -Sql "select current_database(), current_user, now()"
    Section -Title "Test connectivity (smartsell_test)" -Content $testConn

    # 2) Presence validation
    $presenceSql = "select datname from pg_database where datname in ('smartsell_main','smartsell_test');"
    $presence = Invoke-PsqlLine -Url $RuntimeUrl -Sql $presenceSql
    Section -Title "Database presence (expects smartsell_main, smartsell_test)" -Content $presence

    # 3) Alembic preflight (extend alembic_version column if needed)
    Ensure-AlembicVersionSize -Url $RuntimeUrl
    Ensure-AlembicVersionSize -Url $TestUrl

    # 3b) Optional explicit stamp (guarded)
    if ($AllowStamp) {
        $warn = "**WARNING: ALLOW_STAMP ENABLED — stamping head without schema verification. Use only if you know what you are doing.**"
        Section -Title "ALLOW_STAMP warning" -Content $warn
        $stampRuntime = Run-AlembicStampHead -Url $RuntimeUrl -IsTest:$false
        Section -Title "Alembic stamp head (runtime)" -Content $stampRuntime
        $stampTest = Run-AlembicStampHead -Url $TestUrl -IsTest:$true
        Section -Title "Alembic stamp head (test)" -Content $stampTest
    }

    # 4) Alembic upgrades (runtime then test)
    $alembicRuntime = Run-AlembicUpgrade -Url $RuntimeUrl -IsTest:$false
    Section -Title "Alembic upgrade head (runtime)" -Content $alembicRuntime

    $alembicTest = Run-AlembicUpgrade -Url $TestUrl -IsTest:$true
    Section -Title "Alembic upgrade head (test)" -Content $alembicTest

    # 5) Post-upgrade validation
    $runtimeValidation = Check-Tables -Url $RuntimeUrl -Label "runtime"
    $testValidation = Check-Tables -Url $TestUrl -Label "test"

    # 6) Alembic current -v snapshots
    $currentRuntime = Run-AlembicCurrent -Url $RuntimeUrl -IsTest:$false
    Section -Title "Alembic current -v (runtime)" -Content $currentRuntime

    $currentTest = Run-AlembicCurrent -Url $TestUrl -IsTest:$true
    Section -Title "Alembic current -v (test)" -Content $currentTest

    if ($RunUtf8Probe) {
        Write-Info "Running UTF-8 probe on runtime/test"
        $utf8ProbeScript = Join-Path $root "tools/utf8_probe.ps1"
        if (-not (Test-Path $utf8ProbeScript)) {
            throw "UTF-8 probe script not found: $utf8ProbeScript"
        }

        $envBackup = @{ PGCLIENTENCODING = $env:PGCLIENTENCODING }
        try {
            $env:PGCLIENTENCODING = "UTF8"
            $utf8ProbeOutput = & pwsh -ExecutionPolicy Bypass -File $utf8ProbeScript -RuntimeUrl $RuntimeUrl -TestUrl $TestUrl 2>&1
            Section -Title "UTF-8 probe" -Content $utf8ProbeOutput
        } finally {
            if ($null -ne $envBackup.PGCLIENTENCODING) {
                $env:PGCLIENTENCODING = $envBackup.PGCLIENTENCODING
            } else {
                Remove-Item Env:PGCLIENTENCODING -ErrorAction SilentlyContinue
            }
        }
    }
}
catch {
    $hadError = $true
    $failureMessages += $_.Exception.Message
    Section -Title "Failure" -Content ($failureMessages -join "`n")
}
finally {
    $sanRuntime = Mask-UrlPassword $RuntimeUrl
    $sanTest = Mask-UrlPassword $TestUrl

    # 7) Summary
    $report += "## Summary"
    $report += '```'
    $report += "DATABASE_URL=" + $sanRuntime
    $report += "TEST_DATABASE_URL=" + $sanTest
    $report += "tables_runtime=$($runtimeValidation.Count); tables_test=$($testValidation.Count)"
    $report += "key_tables_missing_runtime=[$( ($runtimeValidation.Missing -join ', ') )]"
    $report += "key_tables_missing_test=[$( ($testValidation.Missing -join ', ') )]"
    $report += '```'

    # 8) How to run (inline)
    $report += "## How to run"
    $report += '```'
    $report += "cd /d $root"
    $report += ('$env:DATABASE_URL="' + $sanRuntime + '"; $env:TEST_DATABASE_URL="' + $sanTest + '"; pwsh -ExecutionPolicy Bypass -File tools/db_reset.ps1')
    $report += "# To allow stamping (dangerous): pwsh -ExecutionPolicy Bypass -File tools/db_reset.ps1 -AllowStamp"
    $report += '```'

    $report | Out-File -FilePath $reportPath -Encoding utf8
    Write-Info "Report written to $reportPath"
    if ($hadError) { exit $exitCode }
}
