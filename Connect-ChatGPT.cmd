@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WINOWNER_ROOT=%~dp0"
set "WINOWNER_SCRIPT=%WINOWNER_ROOT%scripts\Connect-ChatGPT.ps1"
set "WINOWNER_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "WINOWNER_EXIT=0"

if not exist "%WINOWNER_SCRIPT%" (
    echo [THẤT BẠI] Không tìm thấy scripts\Connect-ChatGPT.ps1.
    set "WINOWNER_EXIT=2"
    goto finish
)
"%WINOWNER_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%WINOWNER_SCRIPT%" %*
set "WINOWNER_EXIT=%ERRORLEVEL%"

:finish
if "%~1"=="" (
    echo.
    echo Nhấn phím bất kỳ để đóng cửa sổ...
    pause >nul
)
exit /b %WINOWNER_EXIT%
