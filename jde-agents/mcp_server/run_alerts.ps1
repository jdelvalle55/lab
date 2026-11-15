# Runs the Planner alert job once. Called by the scheduled task; loads .env like run_server.ps1.
param([string]$Branch = "30", [switch]$Send)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    $v = ($v -split '\s+#')[0].Trim()
    [Environment]::SetEnvironmentVariable($k.Trim(), $v, "Process")
}
$pyArgs = @("-m", "jde_agent.alerts", "--branch", $Branch)
if ($Send) { $pyArgs += "--send" }
# Out-File -Encoding utf8 so the log is readable outside Notepad (PS 5.1 *>> writes UTF-16)
& .\.venv\Scripts\python.exe @pyArgs 2>&1 | Out-File -FilePath (Join-Path $PSScriptRoot "alerts.log") -Append -Encoding utf8
