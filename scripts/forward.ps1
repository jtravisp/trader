# Evening job (18:00 CT = 19:00 ET): Task Scheduler wrapper for `trader job forward` (src/trader/jobs.py),
# which writes data\logs\forward_<date>.log. Only uv's own errors land in data\logs\scheduler.log.
# Registered as "trader-forward" by scripts\register-tasks.ps1.
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
& "$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader job forward *>> data\logs\scheduler.log
exit $LASTEXITCODE
