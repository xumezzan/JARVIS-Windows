@echo off
rem One click: pull what was merged and reinstall it. Process-only execution policy;
rem no persistent policy change and no elevation.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-Jarvis.ps1"
set JARVIS_UPDATE_EXIT=%errorlevel%
echo.
pause
exit /b %JARVIS_UPDATE_EXIT%
