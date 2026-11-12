# Morning job (08:40 CT = 09:40 ET, weekdays): Task Scheduler wrapper for `trader job gateway`
# (src/trader/jobs.py), which writes data\logs\gateway_<date>.log. Only uv's own errors land in
# data\logs\scheduler.log. Registered as "trader-gateway" by scripts\register-tasks.ps1.
# Holidays: the gateway sees the market closed and does nothing.
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
& "$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader job gateway *>> data\logs\scheduler.log
exit $LASTEXITCODE
