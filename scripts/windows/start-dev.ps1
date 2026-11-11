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

foreach ($port in @($ApiPort, $WebPort)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Pass -ApiPort/-WebPort with free ports (nothing was stopped)."
    }
}
docker compose up -d postgres redis mock-bank | Out-Null
& .venv\Scripts\python.exe scripts\tasks.py migrate | Out-Null

$py = Join-Path $Root ".venv\Scripts\python.exe"
$envLoader = "Get-Content .env | Where-Object { `$_ -match '^[A-Z_]+=.' } | ForEach-Object { `$k,`$v = `$_ -split '=',2; Set-Item -Path ('env:' + `$k) -Value `$v }"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $envLoader; & '$py' -m uvicorn finagent.api.main:create_app --factory --host 127.0.0.1 --port $ApiPort"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $envLoader; `$env:FINAGENT_NOTIFICATION_BACKEND='file'; & '$py' -m finagent.worker"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $envLoader; cd apps\web; `$env:FINAGENT_API_URL='http://127.0.0.1:$ApiPort'; npx next dev -p $WebPort -H 127.0.0.1"
Write-Host "API http://127.0.0.1:$ApiPort/docs  |  Web http://127.0.0.1:$WebPort  |  Mock bank http://127.0.0.1:8100/docs"
