# Runs the Planner alert job once. Called by the scheduled task; loads .env like run_server.ps1.
param([string]$Branch = "30", [switch]$Send)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    $v = ($v -split '\s+#')[0].Trim()
    [Environment]::SetEnvironmentVariable($k.Trim(), $v, "Process")
}
$args = @("-m", "jde_agent.alerts", "--branch", $Branch)
if ($Send) { $args += "--send" }
& .\.venv\Scripts\python.exe @args *>> (Join-Path $PSScriptRoot "alerts.log")
