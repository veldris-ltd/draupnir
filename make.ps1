# DRAUPNIR task runner for Windows, where `make` is not present.
#
#   .\make.ps1 dev
#   .\make.ps1 ci
#   .\make.ps1            # lists every task
#
# This is a shim. The tasks themselves live in tasks.py, so Windows and the
# self-hosted Linux runner execute identical steps.

param(
    [Parameter(Position = 0)]
    [string]$Task
)

$ErrorActionPreference = "Stop"

$python = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $python) {
    $python = (Get-Command python3 -ErrorAction SilentlyContinue)
}
if ($null -eq $python) {
    Write-Error "Python 3.12 or later is required. See docs/CONTRIBUTING.md."
}

if ([string]::IsNullOrWhiteSpace($Task)) {
    & $python.Source (Join-Path $PSScriptRoot "tasks.py") --list
} else {
    & $python.Source (Join-Path $PSScriptRoot "tasks.py") $Task
}
exit $LASTEXITCODE
