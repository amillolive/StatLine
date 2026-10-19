[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$PortalPort = 3000,
    [string]$Python = "",
    [string]$ApiUrl = "",
    [string[]]$PortalOrigin = @(),
    [switch]$CheckOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PythonExecutable = $Python
if (-not $PythonExecutable) { $PythonExecutable = $env:STATLINE_PYTHON }
if (-not $PythonExecutable) {
    $VenvPython = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
    $PythonExecutable = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
}
$LaunchArgs = @((Join-Path $PSScriptRoot "serve.py"), "--python", $PythonExecutable, "--api-port", "$ApiPort", "--portal-port", "$PortalPort")
if ($ApiUrl) { $LaunchArgs += @("--api-url", $ApiUrl) }
foreach ($Origin in $PortalOrigin) { $LaunchArgs += @("--portal-origin", $Origin) }
if ($CheckOnly) { $LaunchArgs += "--check-only" }
& $PythonExecutable @LaunchArgs
exit $LASTEXITCODE
