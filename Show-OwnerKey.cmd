@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe""%PWSH%" -NoProfile -Sta -ExecutionPolicy Bypass -File "%~dp0scripts\Show-OwnerKey.ps1" %*
set "STATUS=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %STATUS%
