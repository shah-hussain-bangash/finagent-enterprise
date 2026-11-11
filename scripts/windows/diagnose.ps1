<#
.SYNOPSIS
  Read-only diagnostics for the local FinAgent stack. Prints NO secrets (only whether keys are set).
#>
param([int]$ApiPort = 8001, [int]$WebPort = 3001)
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
function Check($name, $ok, $detail) { "{0,-34} {1,-5} {2}" -f $name, ($(if ($ok) { "OK" } else { "FAIL" })), $detail }

Check "python 3.12" ((python --version 2>&1) -match "3\.12") (python --version 2>&1)
Check "node >= 22" ((node --version) -match "^v2[2-9]") (node --version)
Check "docker" ([bool](Get-Command docker -ErrorAction SilentlyContinue)) ""
Check ".venv" (Test-Path ".venv\Scripts\python.exe") ""
Check ".env present" (Test-Path ".env") ""
if (Test-Path ".env") {
    $keys = Get-Content .env | Where-Object { $_ -match "^[A-Z_]+=" } | ForEach-Object { ($_ -split "=", 2)[0] }
    foreach ($k in "FINAGENT_DATABASE_URL", "FINAGENT_DEV_JWT_SECRET", "FINAGENT_APP_DB_PASSWORD", "FINAGENT_WEB_SESSION_SECRET") {
        Check "  $k set" ($keys -contains $k) "(value not shown)"
    }
}
$ps = docker compose ps --format "{{.Service}}={{.State}}" 2>$null
foreach ($svc in "postgres", "redis", "mock-bank") { Check "container $svc" ($ps -match "$svc=running") "" }
try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$ApiPort/health/ready" -TimeoutSec 3; Check "api ready" ($r.StatusCode -eq 200) "" }
catch { Check "api ready" $false "not reachable on $ApiPort" }
try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$WebPort/signin" -TimeoutSec 5; Check "web" ($r.StatusCode -eq 200) "" }
catch { Check "web" $false "not reachable on $WebPort" }
try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8100/docs" -TimeoutSec 3; Check "mock bank" ($r.StatusCode -eq 200) "" }
catch { Check "mock bank" $false "not reachable on 8100" }
& .venv\Scripts\python.exe -m alembic current 2>$null | ForEach-Object { Check "alembic revision" ($_ -match "head") $_ }
