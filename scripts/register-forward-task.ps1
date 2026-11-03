# Registers the daily P1 forward job (news + bars + LLM scoring) at 18:00 local (Central) = 19:00 ET.
# Run once:   powershell -ExecutionPolicy Bypass -File scripts\register-forward-task.ps1
# Remove:     schtasks /Delete /TN trader-forward /F
$root = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\forward.ps1`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "trader-forward" -Action $action -Trigger $trigger -Settings $settings `
  -Description "trader P1 forward collection: news, bars, LLM scoring" -Force | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "trader-forward" | Select-Object NextRunTime
