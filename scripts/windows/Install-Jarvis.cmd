@echo off
rem Process-only execution policy; no persistent policy or elevation changes.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Jarvis.ps1"
exit /b %errorlevel%
