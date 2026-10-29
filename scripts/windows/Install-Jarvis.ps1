#requires -Version 5.1
<#
Per-user repository bootstrap. No elevation, policy changes, credentials or live providers.
Run from a trusted checkout: & '.\scripts\windows\Install-Jarvis.ps1'
#>
[CmdletBinding()]
param([ValidatePattern('^[a-f0-9]{40}$')][string]$SourceCommit)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$jarvisLock = $null
$jarvisRoot = $null
$jarvisRunId = [Guid]::NewGuid().ToString('D')
$jarvisReportRoot = $null
$jarvisFallbackReportRoot = $null
$jarvisSavedReportRoot = $null
$python = $null
$jarvisRuntimeReady = $false
$jarvisCurrentPhase = 'host'
$jarvisCancelled = $false
$jarvisBootstrap = [ordered]@{
    schema = 2
    run_id = $jarvisRunId
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    updated_at_utc = [DateTime]::UtcNow.ToString('o')
    finished_at_utc = $null
    status = 'running'
    scope = 'windows_installation_bootstrap'
    windows_mvp_acceptance = 'pending'
    application_report = 'installation-report.json'
    windows_build = $null
    source_commit = $SourceCommit
    phases = @(
        [ordered]@{ id = 'host'; name = 'Windows 11 x64 и интерактивный сеанс'; status = 'not_checked'; error_category = 'host_requirements'; remedy = 'Запустите установщик в 64-битном PowerShell в интерактивном сеансе Windows 11 x64.' },
        [ordered]@{ id = 'managed_root_lock'; name = 'Каталог установки и исключительная блокировка'; status = 'not_checked'; error_category = 'installation_path_or_lock'; remedy = 'Закройте другой установщик. Проверьте доступ к каталогу; не удаляйте чужой каталог и не обходите ссылки или системную политику.' },
        [ordered]@{ id = 'runtime'; name = 'Официальный Python: целостность и запуск'; status = 'not_checked'; error_category = 'runtime_preparation'; remedy = 'Проверьте сеть, свободное место, доверие Windows и существующий Python. Если требуется перезагрузка, выполните её; затем повторите установку.' },
        [ordered]@{ id = 'application_setup'; name = 'Установка приложения и локальная диагностика'; status = 'not_checked'; error_category = 'application_setup'; remedy = 'Прочитайте installation-report.md того же запуска, исправьте указанный этап и повторите установщик. Проверки живых сервисов остаются отдельными.' },
        [ordered]@{ id = 'launcher_shortcut'; name = 'Файл запуска и ярлык в меню «Пуск»'; status = 'not_checked'; error_category = 'launcher_or_shortcut'; remedy = 'Проверьте права на каталог установки и меню «Пуск», затем повторите установку. После успеха отдельно откройте Jarvis через ярлык.' }
    )
}

function Assert-PlainDirectory([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Setup paths must not be links or junctions.'
        }
        $item = $item.Parent
    }
}

function Test-Marker([string]$Path) {
    <#
    Whether the ownership marker is there. The question is asked more than once on purpose:
    Test-Path answers False both for "not there" and for "could not look", and a scanner
    holding the file for a moment is enough to turn an ordinary update into "inspect this
    directory before setup". The invariant is untouched - a directory without the marker
    still stops the install - and only a momentary inability to see it is retried.
    #>
    foreach ($attempt in 1..3) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Assert-PlainEntry([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Setup/report files must not be links or junctions.'
    }
}

function Write-AtomicReport([string]$Path, [string]$Content) {
    Assert-PlainDirectory (Split-Path -Parent $Path)
    Assert-PlainEntry $Path
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    $stream = $null
    $writer = $null
    try {
        $stream = [IO.File]::Open($temporary, 'CreateNew', 'Write', 'None')
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        $writer.Write($Content)
        $writer.Flush()
        $stream.Flush($true)
        $writer.Dispose()
        $writer = $null
        $stream = $null
        Assert-PlainEntry $Path
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary, $Path, [NullString]::Value)
        } else {
            [IO.File]::Move($temporary, $Path)
        }
    } finally {
        if ($null -ne $writer) { $writer.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -ErrorAction SilentlyContinue }
    }
}

function Get-FallbackReportRoot {
    if ($null -eq $script:jarvisFallbackReportRoot) {
        $temporaryRoot = [IO.Path]::GetTempPath()
        Assert-PlainDirectory $temporaryRoot
        $candidate = Join-Path $temporaryRoot ('JarvisInstallReport-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $candidate -ErrorAction Stop | Out-Null
        Assert-PlainDirectory $candidate
        $script:jarvisFallbackReportRoot = $candidate
    }
    return $script:jarvisFallbackReportRoot
}

function Write-BootstrapFiles([string]$Directory) {
    Assert-PlainDirectory $Directory
    $jsonPath = Join-Path $Directory 'installation-bootstrap.json'
    $markdownPath = Join-Path $Directory 'installation-bootstrap.md'
    $statusNames = @{ running = 'Выполняется'; passed = 'Проверено'; failed = 'Ошибка'; cancelled = 'Отменено'; not_checked = 'Не проверено' }
    $lines = @(
        '# Отчёт установки Jarvis',
        '',
        ('Запуск: ' + $jarvisBootstrap.run_id),
        ('Начало (UTC): ' + $jarvisBootstrap.started_at_utc),
        ('Обновлено (UTC): ' + $jarvisBootstrap.updated_at_utc),
        ('Результат установки: ' + $statusNames[$jarvisBootstrap.status]),
        '',
        '| Этап | Статус |',
        '| --- | --- |'
    )
    foreach ($phase in $jarvisBootstrap.phases) {
        $lines += '| ' + $phase.name + ' | ' + $statusNames[$phase.status] + ' |'
    }
    foreach ($phase in $jarvisBootstrap.phases) {
        if ($phase.status -in @('failed', 'cancelled')) {
            $lines += @('', ('Категория: ' + $phase.error_category), ('Следующий шаг: ' + $phase.remedy))
        }
    }
    $lines += @(
        '',
        'Этот отчёт проверяет только этапы установщика. Создание ярлыка не подтверждает запуск через меню «Пуск».',
        'Подробная локальная диагностика: installation-report.json / installation-report.md с тем же идентификатором запуска. Если идентификатор отличается, файл относится к предыдущей попытке.',
        'Полная приёмка MVP на Windows, реальные микрофон и динамики, Outlook OAuth/доставка и живая модель: НЕ ПРОВЕРЕНО. Выполните docs/WINDOWS_ACCEPTANCE.md.',
        'Проверки не записывают звук, не вводят ключи, не отправляют письма и не заменяют пользовательское подтверждение.'
    )
    Write-AtomicReport $jsonPath ($jarvisBootstrap | ConvertTo-Json -Depth 8)
    Write-AtomicReport $markdownPath ($lines -join [Environment]::NewLine)
    # A complete, transferable document must exist even before Python is available.
    # Use only installer-owned text: never embed exception messages, paths, credentials
    # or arbitrary application-report fields in this conservative fallback.
    $featureNames = @(
        'Зависимости Python и native DLL',
        'Chromium: локальная страница без сети и JavaScript',
        'Загрузка локальной русской модели речи',
        'Разрешения, симуляция, отмена и аудит',
        'Локальный планировщик',
        'Память: запись, чтение, изменение и удаление',
        'Системный credential backend',
        'Обнаружение микрофона, динамика и русского голоса',
        'Видимое окно установленного приложения',
        'Повторный запуск из меню «Пуск»',
        'Windows: приложения и ввод после точного подтверждения',
        'Фактическая публичная поисковая выдача',
        'Запись, распознавание речи и слышимое озвучивание',
        'Outlook: OAuth, черновик, отправка и доставка',
        'Реальный запрос к облачной модели',
        'Дополнительный ElevenLabs Free: API и звук',
        'Полная Windows-приёмка MVP'
    )
    $shareLines = @(
        '# Отчёт установки и проверки Jarvis',
        '',
        'Этот документ можно отправить целиком. Другие файлы рядом с ним не нужны.',
        ('Запуск: ' + $jarvisBootstrap.run_id),
        ('Начало (UTC): ' + $jarvisBootstrap.started_at_utc),
        ('Обновлено (UTC): ' + $jarvisBootstrap.updated_at_utc),
        ('Результат установщика: ' + $statusNames[$jarvisBootstrap.status]),
        '',
        'Сводная диагностика приложения пока недоступна. Ниже приведены только проверенные этапы установщика; функции не считаются рабочими без отдельного результата того же запуска.',
        '',
        '| Этап установки | Статус | Следующий шаг |',
        '| --- | --- | --- |'
    )
    foreach ($phase in $jarvisBootstrap.phases) {
        $remedy = '—'
        if ($phase.status -in @('failed', 'cancelled', 'not_checked')) { $remedy = $phase.remedy }
        if ($phase.id -eq 'application_setup' -and $phase.status -ne 'passed') {
            $remedy = 'Повторите Install-Jarvis.cmd; сохраните этот документ для исправления. Живые проверки потребуют действий пользователя.'
        }
        $shareLines += '| ' + $phase.name + ' | ' + $statusNames[$phase.status] + ' | ' + $remedy + ' |'
    }
    $shareLines += @('', '| Функция | Статус |', '| --- | --- |')
    foreach ($name in $featureNames) { $shareLines += '| ' + $name + ' | НЕ ПРОВЕРЕНО (not_checked) |' }
    $shareLines += @(
        '',
        'Следующий шаг: исправьте указанный этап и повторите Install-Jarvis.cmd. Для живых функций потребуются действия пользователя в приложении: подключение аккаунта, ввод собственного ключа, удержание кнопки записи и точные подтверждения.',
        'Отчёт не подтверждает работу Windows UIA, реального микрофона/динамика, публичного поиска, Outlook или облачной модели.',
        'Проверки не записывают звук, не читают секреты, не отправляют письма и не заменяют пользовательское подтверждение.',
        'Полная приёмка MVP остаётся НЕ ПРОВЕРЕНО. Выполните сценарии docs/WINDOWS_ACCEPTANCE.md.'
    )
    $shareMarkdown = $shareLines -join [Environment]::NewLine
    $escaped = [Net.WebUtility]::HtmlEncode($shareMarkdown)
    $shareHtml = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src ''none''; style-src ''unsafe-inline''; base-uri ''none''; form-action ''none''"><title>Отчёт Jarvis</title><style>body{margin:2rem auto;padding:0 1rem;max-width:80rem;font:16px/1.5 system-ui,sans-serif;color:#17202a;background:#fff}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}@media print{body{margin:0;max-width:none}}</style></head><body><pre>' + $escaped + '</pre></body></html>'
    Write-AtomicReport (Join-Path $Directory 'Jarvis-Report.md') $shareMarkdown
    Write-AtomicReport (Join-Path $Directory 'Jarvis-Report.html') $shareHtml
    $script:jarvisSavedReportRoot = $Directory
    Write-Host ('Jarvis bootstrap reports: ' + $jsonPath + ' ; ' + $markdownPath)
}

function Quote-ReportArgument([string]$Value) {
    # ProcessStartInfo.Arguments uses Windows argument quoting, with no command shell.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Complete-ShareableReport {
    if ($null -eq $script:jarvisSavedReportRoot) { return }
    $reportDirectory = $script:jarvisSavedReportRoot
    $reportPath = Join-Path $reportDirectory 'Jarvis-Report.html'
    if ($script:jarvisRuntimeReady -and $null -ne $script:python -and $null -ne $script:jarvisRoot) {
        $process = $null
        $started = $false
        try {
            $exporter = Join-Path $PSScriptRoot 'export_report.py'
            Assert-PlainDirectory $PSScriptRoot
            Assert-PlainEntry $exporter
            if (-not (Test-Path -LiteralPath $exporter -PathType Leaf)) { throw 'Exporter is unavailable.' }
            $start = [Diagnostics.ProcessStartInfo]::new()
            $start.FileName = $script:python
            $arguments = @('-I', $exporter, '--root', $reportDirectory,
                '--application-root', $script:jarvisRoot, '--run-id', $jarvisRunId)
            $start.Arguments = ($arguments | ForEach-Object { Quote-ReportArgument $_ }) -join ' '
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $start.RedirectStandardOutput = $true
            $start.RedirectStandardError = $true
            $process = [Diagnostics.Process]::new()
            $process.StartInfo = $start
            if (-not $process.Start()) { throw 'Exporter did not start.' }
            $started = $true
            # Drain output directly to a null stream: no raw output in RAM reports,
            # temporary files or console; exporter uses only the Python standard library.
            $stdout = $process.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
            $stderr = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
            if (-not $process.WaitForExit(30000)) { throw 'Exporter timed out.' }
            if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000) -or $process.ExitCode -ne 0) {
                throw 'Exporter did not complete.'
            }
        } catch {
            Write-Warning 'Combined application diagnostics could not be exported. The standalone document includes the installer result; unavailable checks remain not_checked.'
        } finally {
            if ($null -ne $process) {
                try {
                    if ($started -and -not $process.HasExited) {
                        $process.Kill()
                        $process.WaitForExit(5000) | Out-Null
                    }
                } catch { } finally { $process.Dispose() }
            }
        }
    }
    Write-Host ('Send this Jarvis report: ' + [IO.Path]::GetFullPath($reportPath))
}

function Write-BootstrapReport {
    $jarvisBootstrap.updated_at_utc = [DateTime]::UtcNow.ToString('o')
    # Only advertise a document saved for the latest bootstrap state.
    $script:jarvisSavedReportRoot = $null
    if ($null -ne $script:jarvisReportRoot) {
        try {
            Write-BootstrapFiles $script:jarvisReportRoot
            return
        } catch {
            Write-Warning 'The current bootstrap report could not be saved in JarvisInstall; an existing file may be stale. Trying a separate temporary report.'
        }
    }
    try {
        Write-BootstrapFiles (Get-FallbackReportRoot)
    } catch {
        Write-Warning 'No current bootstrap report could be saved. Check writable temporary storage; console output is the only result available.'
    }
}

function Set-BootstrapPhase([string]$Phase, [ValidateSet('running', 'passed', 'failed', 'cancelled', 'not_checked')][string]$Status) {
    $script:jarvisCurrentPhase = $Phase
    foreach ($entry in $jarvisBootstrap.phases) {
        if ($entry.id -eq $Phase) {
            $entry.status = $Status
            break
        }
    }
    Write-BootstrapReport
}

function Test-Runtime([string]$Python, [string]$Version) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    $result = & $Python -I -c 'import sys,struct,ssl,venv,ensurepip; print(chr(46).join(map(str,sys.version_info[:3]))+chr(47)+str(struct.calcsize(chr(80))*8))' 2>$null
    return ($LASTEXITCODE -eq 0 -and $result -eq "$Version/64")
}

try {
    Set-BootstrapPhase 'host' 'running'
    $osBuild = [Environment]::OSVersion.Version.Build
    if ($env:OS -eq 'Windows_NT') { $jarvisBootstrap.windows_build = $osBuild }
    $architecture = (Get-CimInstance Win32_Processor | Select-Object -First 1).Architecture
    if ($env:OS -ne 'Windows_NT' -or $osBuild -lt 22000 -or $architecture -ne 9 -or
        -not [Environment]::Is64BitProcess -or -not [Environment]::UserInteractive) {
        throw 'Use 64-bit PowerShell in an interactive Windows 11 x64 desktop session.'
    }
    Set-BootstrapPhase 'host' 'passed'
    Set-BootstrapPhase 'managed_root_lock' 'running'
    $jarvisRoot = Join-Path $env:LOCALAPPDATA 'JarvisInstall'
    $repository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    Assert-PlainDirectory $env:LOCALAPPDATA
    if (Test-Path -LiteralPath $jarvisRoot) {
        Assert-PlainDirectory $jarvisRoot
        $marker = Join-Path $jarvisRoot '.jarvis-install'
        Assert-PlainEntry $marker
        if (-not (Test-Marker $marker)) {
            throw 'Existing JarvisInstall directory has no ownership marker. Inspect it before setup.'
        }
    } else {
        New-Item -ItemType Directory -Path $jarvisRoot | Out-Null
        [IO.File]::WriteAllText((Join-Path $jarvisRoot '.jarvis-install'), '1')
    }
    # Exclusive OS handle: released after Ctrl+C/crash; no stale PID lock to remove.
    Assert-PlainEntry (Join-Path $jarvisRoot 'setup.lock')
    $jarvisLock = [IO.File]::Open((Join-Path $jarvisRoot 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $jarvisReportRoot = $jarvisRoot
    Set-BootstrapPhase 'managed_root_lock' 'passed'
    Set-BootstrapPhase 'runtime' 'running'
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'assets.json') -Raw | ConvertFrom-Json
    $cache = Join-Path $jarvisRoot 'cache'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    Assert-PlainDirectory $cache
    $runtimeDirectory = Join-Path $jarvisRoot 'runtime'
    $python = Join-Path $runtimeDirectory 'python.exe'
    $runtimeVersion = $manifest.python.version
    # A python.org installer registers one install per minor version. Reuse a matching
    # user-owned registered runtime; never move/replace an unrelated Python installation.
    $registered = Get-ItemProperty -LiteralPath 'HKCU:\Software\Python\PythonCore\3.13\InstallPath' -ErrorAction SilentlyContinue
    if ($null -ne $registered) {
        $registeredPath = $registered.'(default)'
        $candidate = Join-Path $registeredPath 'python.exe'
        if (Test-Runtime $candidate $runtimeVersion) {
            $python = $candidate
        } elseif ([IO.Path]::GetFullPath($registeredPath).TrimEnd('\') -ne $runtimeDirectory.TrimEnd('\')) {
            throw 'A different per-user Python 3.13 is registered. Review it before changing runtime; it was not modified.'
        }
    }
    if (-not (Test-Runtime $python $runtimeVersion)) {
        Write-Host 'Preparing official Python runtime (candidate; Windows acceptance is still required).'
        $installer = Join-Path $cache 'python-amd64.exe'
        $valid = (Test-Path -LiteralPath $installer) -and
            ((Get-Item -LiteralPath $installer).Length -eq $manifest.python.size) -and
            ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -eq $manifest.python.sha256)
        if (-not $valid) {
            $part = "$installer.part"
            if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part }
            $request = [Net.HttpWebRequest]::Create($manifest.python.url)
            if ($request.RequestUri.Scheme -ne 'https' -or $request.RequestUri.Host -ne 'www.python.org') {
                throw 'Python source is not the reviewed official HTTPS host.'
            }
            $request.AllowAutoRedirect = $false
            $request.Proxy = $null
            $request.Timeout = 30000
            $request.ReadWriteTimeout = 30000
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $response = $null; $inputStream = $null; $outputStream = $null
            try {
                $response = $request.GetResponse()
                if ([int]$response.StatusCode -ne 200) { throw 'Python download returned an unexpected status.' }
                $inputStream = $response.GetResponseStream()
                $outputStream = [IO.File]::Open($part, 'CreateNew', 'Write', 'None')
                $buffer = New-Object byte[] 65536
                $total = 0
                $timer = [Diagnostics.Stopwatch]::StartNew()
                while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $total += $count
                    if ($total -gt $manifest.python.size -or $timer.Elapsed.TotalSeconds -gt 300) {
                        throw 'Python download exceeded its size/time limit.'
                    }
                    $outputStream.Write($buffer, 0, $count)
                }
            } finally {
                if ($null -ne $outputStream) { $outputStream.Dispose() }
                if ($null -ne $inputStream) { $inputStream.Dispose() }
                if ($null -ne $response) { $response.Dispose() }
            }
            if ((Get-Item -LiteralPath $part).Length -ne $manifest.python.size -or
                (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.python.sha256) {
                Remove-Item -LiteralPath $part
                throw 'Python SHA-256/size check failed. Rerun setup; do not execute this download.'
            }
            Move-Item -LiteralPath $part -Destination $installer -Force
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Python Software Foundation(,|$)') {
            throw 'Python Authenticode publisher check failed. Check Windows trust/network; do not bypass it.'
        }
        $arguments = '/passive InstallAllUsers=0 Include_launcher=0 InstallLauncherAllUsers=0 PrependPath=0 AssociateFiles=0 Shortcuts=0 Include_test=0 Include_doc=0 Include_tcltk=0 Include_pip=1 TargetDir="' + $runtimeDirectory + '"'
        if ($null -ne $registered) { $arguments = '/repair /passive' }
        $runtimeProcess = Start-Process -FilePath $installer -ArgumentList $arguments -PassThru
        if (-not $runtimeProcess.WaitForExit(600000)) {
            throw 'Python setup timed out. Inspect the visible setup process before rerunning.'
        }
        if ($runtimeProcess.ExitCode -eq 3010) { throw 'Python requests a Windows restart. Restart, then rerun setup.' }
        if ($runtimeProcess.ExitCode -ne 0 -or -not (Test-Runtime $python $runtimeVersion)) {
            throw 'Python setup/repair failed. Check the visible installer and rerun.'
        }
    }
    $jarvisRuntimeReady = $true
    # Packaged hosts may virtualize LOCALAPPDATA. Persist real paths so a Start
    # menu launch outside that host reaches the same runtime and existing data.
    # ASCII JSON survives Windows PowerShell 5.1's OEM stdout decoder even when
    # Python uses UTF-8 and the Windows profile name contains Cyrillic characters.
    $canonicalJson = & $python -I -c 'from pathlib import Path; import json, sys; print(json.dumps(str(Path(sys.argv[1]).resolve()), ensure_ascii=True))' $jarvisRoot
    $canonicalRoot = $canonicalJson | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not [IO.Path]::IsPathRooted($canonicalRoot)) {
        throw 'The physical installation path could not be resolved.'
    }
    $jarvisRoot = $canonicalRoot.Trim()
    $python = Join-Path $jarvisRoot 'runtime\python.exe'
    Set-BootstrapPhase 'runtime' 'passed'
    Set-BootstrapPhase 'application_setup' 'running'
    Write-Host 'Preparing Jarvis. First installation needs network and at least 3 GB free.'
    $installArguments = @('-I', (Join-Path $PSScriptRoot 'install.py'), '--root', $jarvisRoot, '--run-id', $jarvisRunId)
    if ($SourceCommit) { $installArguments += @('--source-commit', $SourceCommit) }
    & $python @installArguments
    if ($LASTEXITCODE -eq 130) {
        $jarvisCancelled = $true
        throw 'Application setup was cancelled. Rerun to continue; user data is preserved.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Application setup did not pass. Resolve the reported step and rerun this script.' }
    # The slot's real Electron startup must include the click-through Ghost surface
    # before the stable desktop shortcut is refreshed. The installer already binds
    # startup.json to the launched process; this is an additional feature check.
    $startup = Get-Content -LiteralPath (Join-Path $jarvisRoot 'startup.json') -Raw | ConvertFrom-Json
    if (-not ($startup.PSObject.Properties.Name -contains 'ghost_cursor') -or $startup.ghost_cursor -ne $true) {
        throw 'The installed slot did not initialize Ghost Cursor. Rebuild desktop assets and reinstall.'
    }
    Set-BootstrapPhase 'application_setup' 'passed'
    Set-BootstrapPhase 'launcher_shortcut' 'running'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Launch-Jarvis.pyw') -Destination $jarvisRoot -Force
    # Stdlib, read-only Flight Recorder remains usable even when app dependencies fail.
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'diagnose_failures.py') -Destination $jarvisRoot -Force
    # The icon lives beside the launcher rather than inside a slot: slots are retired on
    # update, and a shortcut pointing into a retired one loses its picture.
    Copy-Item -LiteralPath (Join-Path $repository 'src\jarvis\ui\assets\jarvis.ico') -Destination $jarvisRoot -Force
    $shell = New-Object -ComObject WScript.Shell
    # Both places, and written the same way from one description, so they cannot drift
    # apart on an update. The Start menu is where Windows expects an application to be;
    # the desktop is where the owner asked for it, because searching the Start menu for
    # something you open every day is a small tax paid every day.
    $target = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    $arguments = '-I "' + (Join-Path $jarvisRoot 'Launch-Jarvis.pyw') + '"'
    $icon = (Join-Path $jarvisRoot 'jarvis.ico') + ',0'
    foreach ($folder in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
        if (-not $folder -or -not (Test-Path -LiteralPath $folder)) { continue }
        $shortcut = $shell.CreateShortcut((Join-Path $folder 'Jarvis.lnk'))
        $shortcut.TargetPath = $target
        $shortcut.Arguments = $arguments
        $shortcut.WorkingDirectory = $jarvisRoot
        $shortcut.Description = 'Jarvis - local assistant with Ghost Cursor'
        $shortcut.IconLocation = $icon
        $shortcut.Save()
    }
    Set-BootstrapPhase 'launcher_shortcut' 'passed'
    $jarvisBootstrap.status = 'passed'
    $jarvisBootstrap.finished_at_utc = [DateTime]::UtcNow.ToString('o')
    Write-BootstrapReport
    Write-Host 'Jarvis window observed; Start menu and desktop shortcuts created. Native MVP acceptance remains pending.'
} catch {
    $outcome = 'failed'
    if ($jarvisCancelled -or $_.Exception -is [System.Management.Automation.PipelineStoppedException]) { $outcome = 'cancelled' }
    $jarvisBootstrap.status = $outcome
    $jarvisBootstrap.finished_at_utc = [DateTime]::UtcNow.ToString('o')
    Set-BootstrapPhase $jarvisCurrentPhase $outcome
    Write-Error ('Jarvis setup stopped: ' + $_.Exception.Message) -ErrorAction Continue
    if ($outcome -eq 'cancelled') { exit 130 }
    exit 1
} finally {
    # Ctrl+C can skip catch. Preserve an interrupted result before releasing the lock.
    if ($jarvisBootstrap.status -eq 'running') {
        $jarvisBootstrap.status = 'cancelled'
        $jarvisBootstrap.finished_at_utc = [DateTime]::UtcNow.ToString('o')
        Set-BootstrapPhase $jarvisCurrentPhase 'cancelled'
    }
    try { Complete-ShareableReport }
    finally { if ($null -ne $jarvisLock) { $jarvisLock.Dispose() } }
}
