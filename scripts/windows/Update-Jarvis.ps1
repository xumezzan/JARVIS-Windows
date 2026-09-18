#requires -Version 5.1
<#
Bring this checkout up to date with main and reinstall, so what starts from the Start menu
is what was merged. No elevation, no policy changes, no credentials.

Safe by construction: it never discards work. Uncommitted changes, a branch that is not
main, and a history that cannot fast-forward all stop the update with a message instead of
being overwritten.

Run from a trusted checkout: & '.\scripts\windows\Update-Jarvis.ps1'
#>
[CmdletBinding()]
param([switch]$SkipInstall)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Use-Git([string[]]$Arguments) {
    $output = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed: $output" }
    return $output
}

try {
    $repository = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    Set-Location -LiteralPath $repository
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git is not installed or not on PATH. Install Git and run this again.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repository '.git'))) {
        throw "This is not a git checkout: $repository"
    }

    $branch = (Use-Git @('rev-parse', '--abbrev-ref', 'HEAD')).Trim()
    if ($branch -ne 'main') {
        throw "You are on '$branch', not main. Finish or switch off that branch first; nothing was changed."
    }

    $dirty = Use-Git @('status', '--porcelain')
    if ($dirty) {
        Write-Host 'These files have uncommitted changes:'
        $dirty | ForEach-Object { Write-Host "  $_" }
        throw 'Commit or put them aside first. Nothing was changed.'
    }

    $before = (Use-Git @('rev-parse', 'HEAD')).Trim()
    Write-Host 'Fetching what has been merged...'
    Use-Git @('fetch', '--prune', 'origin') | Out-Null
    # Fast-forward only: an update must never rewrite local history to make itself possible.
    Use-Git @('merge', '--ff-only', 'origin/main') | Out-Null
    $after = (Use-Git @('rev-parse', 'HEAD')).Trim()

    if ($before -eq $after) {
        Write-Host 'Already up to date.'
    } else {
        Write-Host ''
        Write-Host 'New in this update:'
        Use-Git @('log', '--no-merges', '--pretty=format:  %s', "$before..$after") |
            ForEach-Object { Write-Host $_ }
        Write-Host ''
    }

    if ($SkipInstall) {
        Write-Host 'Skipping the install, as asked.'
        exit 0
    }

    $running = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$env:LOCALAPPDATA\JarvisInstall\*" }
    if ($running) {
        # Setup leaves Jarvis running on purpose, and a running copy holds the files the
        # next install has to replace. Say it here rather than failing three steps later.
        throw 'Close every open Jarvis window first, then run this again. Nothing was changed.'
    }

    Write-Host 'Installing what was merged...'
    & (Join-Path $PSScriptRoot 'Install-Jarvis.cmd')
    if ($LASTEXITCODE -ne 0) { throw 'Installation did not pass; the previous version is untouched.' }
    Write-Host ''
    Write-Host 'Updated. Start Jarvis from the Start menu to test it.'
    exit 0
} catch {
    Write-Error ("Update stopped: " + $_.Exception.Message) -ErrorAction Continue
    exit 1
}
