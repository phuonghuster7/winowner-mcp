@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
set "RUNNER=%ROOT%scripts\Run-WinOwnerMcp.py"
if not exist "%PYW%" (
    echo [THẤT BẠI] Chưa cài đặt WinOwner MCP. Vui lòng chạy Install-WinOwner.cmd trước.
    if "%~1"=="" pause >nul
    exit /b 1
)
if not exist "%RUNNER%" (
    echo [THẤT BẠI] Thiếu scripts\Run-WinOwnerMcp.py.
    if "%~1"=="" pause >nul
    exit /b 2
)
start "" "%PYW%" -u "%RUNNER%" --open-setup %*
exit /b 0
