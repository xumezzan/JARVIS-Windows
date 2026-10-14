#requires -Version 5.1
<# Explicit repository update. Local changes stay intact; only published main is installed. #>
[CmdletBinding()]
param([switch]$SkipInstall)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repository = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$canonical = 'https://github.com/xumezzan/JARVIS-Windows.git'
$reportDirectory = $null
$phase = 'preflight'
$update = [ordered]@{
    schema = 1
    run_id = [Guid]::NewGuid().ToString('D')
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    finished_at_utc = $null
    status = 'running'
    phase = 'preflight'
    source_repository = 'xumezzan/JARVIS-Windows'
    source_branch = 'main'
    previous_checkout_commit = $null
    source_commit = $null
    checkout_mode = $null
    source_directory = $null
    installation_run_id = $null
    windows_mvp_acceptance = 'pending'
}

function Save-UpdateReport {
    if ($null -eq $reportDirectory) { return }
    $update.phase = $script:phase
    $lines = @(
        '# JARVIS — обновление из GitHub', '',
        ('Статус: ' + $update.status),
        ('Этап: ' + $update.phase),
        ('Запуск: ' + $update.run_id),
        ('Начало (UTC): ' + $update.started_at_utc),
        ('Окончание (UTC): ' + $update.finished_at_utc), '',
        ('Источник: ' + $update.source_repository + ', main'),
        ('Прежний коммит копии: ' + $update.previous_checkout_commit),
        ('Полученный коммит: ' + $update.source_commit),
        ('Способ: ' + $update.checkout_mode),
        ('Каталог проверяемых исходников: ' + $update.source_directory),
        ('Запуск диагностики установки: ' + $update.installation_run_id), '',
        'updated означает, что установка и её автоматическая диагностика прошли; полная Windows-приёмка остаётся отдельной.',
        'source_ready означает только получение исходников: приложение не устанавливалось.',
        'При отказе проверьте сеть/Git и указанный этап; сохраните свои изменения и повторите Update-Jarvis.cmd.',
        'Для ошибки установки прочитайте installation-report.md и installation-bootstrap.md того же запуска.',
        'Нельзя считать failed или running обновлением; уже выполненная активация не откатывается автоматически.'
    )
    foreach ($entry in @(
        @{ name = 'update-report.json'; value = ($update | ConvertTo-Json -Depth 4) },
        @{ name = 'update-report.md'; value = ($lines -join [Environment]::NewLine) }
    )) {
        $path = Join-Path $reportDirectory $entry.name
        $temporary = $path + '.tmp'
        [IO.File]::WriteAllText($temporary, $entry.value, [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($path)) { [IO.File]::Replace($temporary, $path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $path) }
    }
}

function Quote-Argument([string]$Value) {
    # ProcessStartInfo.Arguments uses Windows quoting; no shell or interpolation executes it.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Use-Git([string[]]$Arguments, [switch]$AllowExitOne) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:gitExecutable
    $allArguments = @('-c', ('core.hooksPath=' + (Join-Path $reportDirectory 'empty-hooks')),
        '-c', 'http.sslVerify=true', '-c', 'http.proxy=', '-c', 'credential.helper=',
        '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-c', 'core.fsmonitor=false',
        '-c', 'core.longpaths=true', '-C', $repository) + $Arguments
    $start.Arguments = ($allArguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $start.EnvironmentVariables['GIT_CONFIG_COUNT'] = '0'
    foreach ($name in @('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_NAMESPACE', 'GIT_CONFIG',
        'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_NOSYSTEM',
        'GIT_SSH_COMMAND', 'GIT_SSH', 'GIT_ASKPASS', 'SSH_ASKPASS', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')) {
        $start.EnvironmentVariables.Remove($name)
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $started = $false
    try {
        if (-not $process.Start()) { throw 'Git could not start.' }
        $started = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(180000)) { throw 'Git check timed out; update was not completed.' }
        $output = $stdout.GetAwaiter().GetResult()
        $errorOutput = $stderr.GetAwaiter().GetResult()
        if ($output.Length -gt 2000000 -or $errorOutput.Length -gt 2000000) {
            throw 'Git returned excessive output.'
        }
        if ($AllowExitOne -and $process.ExitCode -eq 1) { return $null }
        if ($process.ExitCode -ne 0) { throw 'Git check failed; inspect network, repository and the named phase.' }
        return $output.Trim()
    } finally {
        try {
            if ($started -and -not $process.HasExited) {
                if ($env:OS -eq 'Windows_NT') {
                    $killer = $null
                    try {
                        $killer = [Diagnostics.Process]::Start(
                            (Join-Path $env:SystemRoot 'System32\taskkill.exe'),
                            ('/PID ' + $process.Id + ' /T /F'))
                        if (-not $killer.WaitForExit(15000)) { $killer.Kill() }
                    } catch { } finally { if ($null -ne $killer) { $killer.Dispose() } }
                }
                if (-not $process.HasExited) { $process.Kill() }
                $process.WaitForExit(15000) | Out-Null
            }
        } finally { $process.Dispose() }
    }
}

function Test-InstallationReports([object]$Installation, [object]$Bootstrap, [object]$Active, [string]$Commit) {
    $installationId = [Guid]::Empty
    return ($Installation.source_commit -eq $Commit -and $Bootstrap.source_commit -eq $Commit -and
        $Active.source_commit -eq $Commit -and $Active.slot -eq $Installation.slot -and
        $Installation.status -eq 'automatic_checks_passed' -and $Bootstrap.status -eq 'passed' -and
        $Installation.run_id -eq $Bootstrap.run_id -and
        [Guid]::TryParse([string]$Installation.run_id, [ref]$installationId) -and
        $installationId -ne [Guid]::Empty)
}

try {
    $reportDirectory = Join-Path ([IO.Path]::GetTempPath()) ('JarvisUpdate-' + $update.run_id)
    New-Item -ItemType Directory -Path $reportDirectory -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $reportDirectory 'empty-hooks') | Out-Null
    Save-UpdateReport
    Write-Host ('Update report: ' + (Join-Path $reportDirectory 'update-report.md'))
    $gitCommand = Get-Command git -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $gitCommand) { throw 'Git is unavailable. Codex must prepare the official source checkout; Python/pip preparation is not required.' }
    $script:gitExecutable = $gitCommand.Source
    if (-not (Test-Path -LiteralPath (Join-Path $repository '.git'))) { throw 'Open the complete trusted Git checkout of Jarvis.' }
    $remote = Use-Git @('remote', 'get-url', 'origin')
    if ($remote -notin @($canonical, 'https://github.com/xumezzan/JARVIS-Windows', 'git@github.com:xumezzan/JARVIS-Windows.git')) {
        throw 'This checkout is not connected to the expected Jarvis repository; no remote was changed.'
    }
    if (Use-Git @('config', '--get-regexp', '^url\..*\.insteadof$') -AllowExitOne) {
        throw 'Git URL rewrites require review before downloading the official source; no configuration was changed.'
    }
    if (-not $SkipInstall) {
        $running = Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -like "$env:LOCALAPPDATA\JarvisInstall\*" }
        if ($running) { throw 'Close the Jarvis windows before updating; no source or installation was changed.' }
    }
    $before = Use-Git @('rev-parse', 'HEAD')
    if ($before -notmatch '^[a-f0-9]{40}$') { throw 'Invalid source commit.' }
    $branch = Use-Git @('rev-parse', '--abbrev-ref', 'HEAD')
    $dirty = Use-Git @('status', '--porcelain')
    $update.previous_checkout_commit = $before
    $phase = 'fetch_main'
    Save-UpdateReport
    Write-Host 'Fetching published main from the official Jarvis repository...'
    Use-Git @('fetch', '--no-tags', $canonical, '+refs/heads/main:refs/remotes/origin/main') | Out-Null
    $after = Use-Git @('rev-parse', 'refs/remotes/origin/main')
    if ($after -notmatch '^[a-f0-9]{40}$') { throw 'Invalid fetched commit.' }
    $update.source_commit = $after
    $phase = 'prepare_source'
    Save-UpdateReport
    $ancestor = Use-Git @('merge-base', '--is-ancestor', $before, $after) -AllowExitOne
    if ($branch -eq 'main' -and -not $dirty -and $null -ne $ancestor) {
        Use-Git @('merge', '--ff-only', $after) | Out-Null
        $source = $repository
        $update.checkout_mode = 'fast_forward'
    } else {
        $source = Join-Path $reportDirectory 'source'
        Use-Git @('worktree', 'add', '--detach', $source, $after) | Out-Null
        $update.checkout_mode = 'isolated_checkout'
    }
    $update.source_directory = $source
    Save-UpdateReport
    Write-Host ('Published source commit: ' + $after)
    if ($SkipInstall) {
        $update.status = 'source_ready'
        $update.finished_at_utc = [DateTime]::UtcNow.ToString('o')
        Save-UpdateReport
        Write-Host 'Source is ready; application installation was explicitly skipped.'
        exit 0
    }
    $phase = 'install_and_diagnose'
    Save-UpdateReport
    if ((Use-Git @('-C', $source, 'rev-parse', 'HEAD')) -ne $after -or
        (Use-Git @('-C', $source, 'status', '--porcelain'))) {
        throw 'The selected source changed before installation; no install was started.'
    }
    & (Join-Path $source 'scripts\windows\Install-Jarvis.cmd') -SourceCommit $after
    if ($LASTEXITCODE -ne 0) { throw 'Installation did not pass. Inspect its current reports; do not assume automatic rollback.' }
    $installationRoot = Join-Path $env:LOCALAPPDATA 'JarvisInstall'
    $installation = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $installationRoot 'installation-report.json') -Raw | ConvertFrom-Json
    $bootstrap = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $installationRoot 'installation-bootstrap.json') -Raw | ConvertFrom-Json
    $active = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $installationRoot 'active.json') -Raw | ConvertFrom-Json
    if ((Use-Git @('-C', $source, 'rev-parse', 'HEAD')) -ne $after -or
        (Use-Git @('-C', $source, 'status', '--porcelain')) -or
        -not (Test-InstallationReports $installation $bootstrap $active $after)) {
        throw 'The current reports do not verify this source commit and installation attempt.'
    }
    $update.installation_run_id = $installation.run_id
    $update.status = 'updated'
    $update.finished_at_utc = [DateTime]::UtcNow.ToString('o')
    Save-UpdateReport
    Write-Host 'Published version installed and automatically checked. Inspect its real window and launcher; full MVP acceptance remains separate.'
    exit 0
} catch {
    $update.status = 'failed'
    $update.finished_at_utc = [DateTime]::UtcNow.ToString('o')
    try { Save-UpdateReport } catch { Write-Warning 'Update report could not be saved; a prior status is not success.' }
    Write-Error ('Update stopped at ' + $phase + '. ' + $_.Exception.Message) -ErrorAction Continue
    exit 1
}
