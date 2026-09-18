#requires -Version 5.1
<#
Per-user repository bootstrap. No elevation, policy changes, credentials or live providers.
Run from a trusted checkout: & '.\scripts\windows\Install-Jarvis.ps1'
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$jarvisLock = $null
$jarvisRoot = Join-Path $env:LOCALAPPDATA 'JarvisInstall'

function Assert-PlainDirectory([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Setup paths must not be links or junctions.'
        }
        $item = $item.Parent
    }
}

function Test-Runtime([string]$Python, [string]$Version) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    $result = & $Python -I -c 'import sys,struct,ssl,venv,ensurepip; print(chr(46).join(map(str,sys.version_info[:3]))+chr(47)+str(struct.calcsize(chr(80))*8))' 2>$null
    return ($LASTEXITCODE -eq 0 -and $result -eq "$Version/64")
}

try {
    $osBuild = [Environment]::OSVersion.Version.Build
    $architecture = (Get-CimInstance Win32_Processor | Select-Object -First 1).Architecture
    if ($env:OS -ne 'Windows_NT' -or $osBuild -lt 22000 -or $architecture -ne 9 -or
        -not [Environment]::Is64BitProcess -or -not [Environment]::UserInteractive) {
        throw 'Use 64-bit PowerShell in an interactive Windows 11 x64 desktop session.'
    }
    $repository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'assets.json') -Raw | ConvertFrom-Json
    Assert-PlainDirectory $env:LOCALAPPDATA
    if (Test-Path -LiteralPath $jarvisRoot) {
        Assert-PlainDirectory $jarvisRoot
        if (-not (Test-Path -LiteralPath (Join-Path $jarvisRoot '.jarvis-install'))) {
            throw 'Existing JarvisInstall directory has no ownership marker. Inspect it before setup.'
        }
    } else {
        New-Item -ItemType Directory -Path $jarvisRoot | Out-Null
        [IO.File]::WriteAllText((Join-Path $jarvisRoot '.jarvis-install'), '1')
    }
    # Exclusive OS handle: released after Ctrl+C/crash; no stale PID lock to remove.
    $jarvisLock = [IO.File]::Open((Join-Path $jarvisRoot 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
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
    Write-Host 'Preparing Jarvis. First installation needs network and at least 3 GB free.'
    & $python -I (Join-Path $PSScriptRoot 'install.py') --root $jarvisRoot
    if ($LASTEXITCODE -ne 0) { throw 'Application setup did not pass. Resolve the reported step and rerun this script.' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Launch-Jarvis.pyw') -Destination $jarvisRoot -Force
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
        $shortcut.Description = 'Jarvis - local assistant'
        $shortcut.IconLocation = $icon
        $shortcut.Save()
    }
    Write-Host 'Jarvis window observed; Start menu and desktop shortcuts created. Native MVP acceptance remains pending.'
} catch {
    Write-Error ('Jarvis setup stopped: ' + $_.Exception.Message) -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $jarvisLock) { $jarvisLock.Dispose() }
}
