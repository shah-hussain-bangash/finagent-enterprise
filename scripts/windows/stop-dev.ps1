<#
.SYNOPSIS
  Stop ONLY this project's Docker services (data volumes are kept). Host API/worker/web windows are
  closed by you; this script never touches other projects' containers.
#>
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
docker compose stop
Write-Host "FinAgent containers stopped (volumes kept). Close the API/worker/web windows yourself."
