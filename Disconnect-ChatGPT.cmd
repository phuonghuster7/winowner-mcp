@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "BOOTSTRAP=%~dp0scripts\Bootstrap-WindowsMcp.ps1"
if not exist "%BOOTSTRAP%" exit /b 2
echo Disconnects only this installation tunnel task. Local MCP service and credentials are unchanged.
choice /C YN /N /M "Disconnect this tunnel? [Y/N] "
if errorlevel 2 exit /b 2
if not errorlevel 1 exit /b 1
"%PWSH%" -NoLogo -NoProfile -File "%BOOTSTRAP%" -Remove -DisconnectTunnel -TunnelOnly %*
set "STATUS=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %STATUS%
