@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Connect-ChatGPT.ps1" %*
set "STATUS=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %STATUS%