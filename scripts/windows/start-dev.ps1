<#
.SYNOPSIS
  Start the local stack on Windows: dependencies in Docker, API + worker + web on the host.
.DESCRIPTION
  Opens three windows (API on 127.0.0.1:<ApiPort>, worker, web on 127.0.0.1:<WebPort>). Reads .env.
  Ports default to 8001/3001 because 8000/3000 are commonly used by other projects.
  Sign in at http://127.0.0.1:<WebPort>/signin with a SYNTHETIC identity (local issuer only).
#>
param([int]$ApiPort = 8001, [int]$WebPort = 3001)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
if (-not (Test-Path ".env")) { throw "Run scripts\windows\setup.ps1 first." }
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
    throw "The project Python environment is missing. Run scripts\windows\setup.ps1 before starting FinAgent."
}
& $py --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The project Python environment cannot start. Repair it before starting FinAgent." }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not available on PATH." }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { throw "Node.js and npx are required to start the web portal." }

foreach ($port in @($ApiPort, $WebPort)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Pass -ApiPort/-WebPort with free ports (nothing was stopped)."
    }
}
docker compose up -d postgres redis mock-bank | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not start the local dependencies. Check Docker and the Compose output above." }
Write-Host "Waiting for the local PostgreSQL service..."
$databaseReady = $false
for ($i = 0; $i -lt 60; $i++) {
    docker compose exec -T postgres pg_isready -U finagent *> $null
    if ($LASTEXITCODE -eq 0) { $databaseReady = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $databaseReady) { throw "PostgreSQL did not become ready. The application services were not started." }
& $py scripts\tasks.py migrate
if ($LASTEXITCODE -ne 0) { throw "Database migration failed. The API, worker and portal were not started." }

$envLoader = "Get-Content .env | Where-Object { `$_ -match '^[A-Z_]+=.' } | ForEach-Object { `$k,`$v = `$_ -split '=',2; Set-Item -Path ('env:' + `$k) -Value `$v }"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $envLoader; & '$py' -m uvicorn finagent.api.main:create_app --factory --host 127.0.0.1 --port $ApiPort"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $envLoader; `$env:FINAGENT_NOTIFICATION_BACKEND='file'; & '$py' -m finagent.worker"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $envLoader; cd apps\web; `$env:FINAGENT_API_URL='http://127.0.0.1:$ApiPort'; npx next dev -p $WebPort -H 127.0.0.1"
Write-Host "API http://127.0.0.1:$ApiPort/docs  |  Web http://127.0.0.1:$WebPort  |  Mock bank http://127.0.0.1:8100/docs"
