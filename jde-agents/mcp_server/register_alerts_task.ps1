# Registers "JDE Planner Alerts" to run hourly, 07:00-19:00, Mon-Fri, as the current user (S4U,
# no stored password). Remove with: Unregister-ScheduledTask -TaskName "JDE Planner Alerts"
param([string]$Branch = "30", [switch]$Send, [int]$EveryMinutes = 60)
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "run_alerts.ps1"
$argList = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Branch $Branch"
if ($Send) { $argList += " -Send" }
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 7:00am
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 7:00am `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Hours 12)).Repetition
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "JDE Planner Alerts" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName "JDE Planner Alerts" | Select-Object TaskName, State
