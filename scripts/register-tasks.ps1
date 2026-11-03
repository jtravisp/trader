# Registers both scheduled jobs (local time = Central; both shift with DST like New York):
#   trader-forward : daily 18:00 CT  - forward data + LLM scoring + tomorrow's target portfolio
#   trader-gateway : Mon-Fri 08:40 CT - risk gateway submits the target to the PAPER account (D9, D19)
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
# Remove:    schtasks /Delete /TN trader-forward /F ; schtasks /Delete /TN trader-gateway /F
$root = Split-Path -Parent $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
function Register-Job($name, $script, $trigger, $desc) {
  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\$script`"" -WorkingDirectory $root
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description $desc -Force |
    Select-Object TaskName, State
}
Register-Job "trader-forward" "forward.ps1" (New-ScheduledTaskTrigger -Daily -At 6:00PM) "trader: forward data, LLM scoring, next target"
Register-Job "trader-gateway" "gateway.ps1" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:40AM) "trader: risk gateway executes target on paper"
Get-ScheduledTask -TaskName trader-* | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
