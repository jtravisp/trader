# Daily forward-collection job (P1). Registered in Task Scheduler as "trader-forward".
# Logs to data\logs\forward_<date>.log. Remove with: schtasks /Delete /TN trader-forward /F
$ErrorActionPreference = "Continue"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
$log = "data\logs\forward_$(Get-Date -Format yyyy-MM-dd).log"
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
& "$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader forward *>> $log
exit $LASTEXITCODE
