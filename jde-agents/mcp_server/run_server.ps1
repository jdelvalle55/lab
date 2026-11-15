# Starts the Planner agent MCP server on vmwindows. Loads .env next to this script.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    $v = ($v -split '\s+#')[0].Trim()
    [Environment]::SetEnvironmentVariable($k.Trim(), $v, "Process")
}
& .\.venv\Scripts\python.exe -m jde_agent.server
