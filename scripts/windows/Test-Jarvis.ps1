#requires -Version 5.1
<#
Developer verification after setup. Native apps and hardware require explicit switches.
Live Outlook acceptance is manual: docs/MILESTONE_8.md. No automatic live send.
#>
[CmdletBinding()]
param([switch]$NativeApps, [switch]$Voice, [switch]$LiveModel)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Check([string[]]$Arguments) {
    & $script:testPython @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Verification failed. Inspect the preceding check.' }
}

$repository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$installRoot = Join-Path $env:LOCALAPPDATA 'JarvisInstall'
$active = Get-Content -LiteralPath (Join-Path $installRoot 'active.json') -Raw | ConvertFrom-Json
if ($active.slot -notin @('a', 'b') -or $active.model -ne 'vosk-model-small-ru-0.22') {
    throw 'Invalid installation pointer. Repair setup first.'
}
$slot = Join-Path $installRoot ('slots\' + $active.slot)
$installedPython = Join-Path $slot 'venv\Scripts\python.exe'
$script:testPython = Join-Path $repository '.venv-acceptance\Scripts\python.exe'
Push-Location -LiteralPath $repository
try {
    if (-not (Test-Path -LiteralPath $script:testPython)) {
        & $installedPython -I -m venv (Join-Path $repository '.venv-acceptance')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated acceptance environment.' }
    }
    Invoke-Check @('-m', 'pip', 'install', '-e', '.[dev,voice]')
    # Acceptance may resolve a different Playwright version; use its own browser cache.
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repository '.venv-acceptance\browsers'
    Invoke-Check @('-m', 'playwright', 'install', 'chromium')
    $env:JARVIS_VOSK_MODEL = Join-Path $slot ('models\' + $active.model)
    $env:QT_QPA_PLATFORM = 'offscreen'
    Invoke-Check @('-m', 'pytest')
    Invoke-Check @('-m', 'ruff', 'check', '.')
    Invoke-Check @('-m', 'ruff', 'format', '--check', '.')
    Invoke-Check @('-m', 'mypy')
    Invoke-Check @('-m', 'pip', 'check')
    $env:QT_QPA_PLATFORM = 'windows'
    Invoke-Check @('-m', 'pytest', 'tests/integration', '-q')
    Invoke-Check @('-m', 'jarvis', '--smoke-test')
    if ($NativeApps) {
        Invoke-Check @('-m', 'pytest', 'tests/e2e/test_windows_acceptance.py', '--run-windows', '-v')
    }
    if ($Voice) {
        Invoke-Check @('-m', 'pytest', 'tests/e2e/test_voice_acceptance.py', '--run-voice', '-v')
    }
    if ($LiveModel) {
        Invoke-Check @('-m', 'pytest', 'tests/e2e/test_model_acceptance.py', '--run-model', '-v')
    }
    Invoke-Check @('-m', 'build')
    Write-Host 'Requested checks passed. Skips are pending, not acceptance. Complete docs/WINDOWS_ACCEPTANCE.md.'
} finally {
    Pop-Location
}
