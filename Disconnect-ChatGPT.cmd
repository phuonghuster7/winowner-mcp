@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "BOOTSTRAP=%~dp0scripts\Bootstrap-WindowsMcp.ps1"
if not exist "%BOOTSTRAP%" exit /b 2
echo Disconnects only this installation tunnel task. Local MCP service and credentials are unchanged.
set "SKIP_CONFIRM=0"
for %%A in (%*) do (
    if /I "%%~A"=="-DryRun" set "SKIP_CONFIRM=1"
    if /I "%%~A"=="-Yes" set "SKIP_CONFIRM=1"
    if /I "%%~A"=="/Y" set "SKIP_CONFIRM=1"
)
if "%SKIP_CONFIRM%"=="0" (
    choice /C YN /N /M "Disconnect this tunnel? [Y/N] "
    if errorlevel 2 exit /b 2
    if not errorlevel 1 exit /b 1
)
set "FWD_ARGS="
for %%A in (%*) do (
    if /I not "%%~A"=="-Yes" if /I not "%%~A"=="/Y" call set "FWD_ARGS=%%FWD_ARGS%% %%A"
)
"%PWSH%" -NoLogo -NoProfile -File "%BOOTSTRAP%" -Remove -DisconnectTunnel -TunnelOnly %FWD_ARGS%
set "STATUS=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %STATUS%
