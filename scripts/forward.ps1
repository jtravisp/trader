# Evening job (18:00 CT = 19:00 ET): forward data collection + LLM scoring, then tomorrow's target.
# Registered as "trader-forward" by scripts\register-tasks.ps1.
$ErrorActionPreference = "Continue"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
$log = "data\logs\forward_$(Get-Date -Format yyyy-MM-dd).log"
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
& "$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader forward *>> $log
$fwd = $LASTEXITCODE
& "$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader live report *>> $log
&"$env:USERPROFILE\.local\bin\uv.exe" run --no-sync trader live target *>> $log
exit $fwd
