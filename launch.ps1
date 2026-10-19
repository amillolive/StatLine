# Compatibility wrapper, delegates to the canonical Python launcher.
& (Join-Path $PSScriptRoot "scripts/launch.ps1") @args
exit $LASTEXITCODE
