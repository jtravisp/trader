# Morning job (08:40 CT = 09:40 ET, weekdays): the risk gateway executes the latest target on PAPER.
# Registered as "trader-gateway" by scripts\register-tasks.ps1. Holidays: the gateway sees the
# market closed and does nothing.
$ErrorActionPreference = "Continue"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
$log = "data\logs\gateway_$(Get-Date -Format yyyy-MM-dd).log"
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
& "$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader live execute --submit *>> $log
exit $LASTEXITCODE
