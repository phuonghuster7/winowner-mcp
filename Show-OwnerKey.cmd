@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WINOWNER_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "WINOWNER_SHOW=%~dp0scripts\Show-OwnerKey.ps1"
set "WINOWNER_EXIT=0"

echo.
echo ===============================================
echo   WinOwner MCP - SAO CHÉP KHÓA CHỦ MÁY
echo ===============================================
echo.
if not exist "%WINOWNER_SHOW%" (
    echo [THẤT BẠI] Thiếu file scripts\Show-OwnerKey.ps1.
    set "WINOWNER_EXIT=2"
    goto finish
)
"%WINOWNER_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%WINOWNER_SHOW%" %*
set "WINOWNER_EXIT=%ERRORLEVEL%"
:finish
if "%~1"=="" (
    echo.
    echo Nhấn phím bất kỳ để đóng cửa sổ...
    pause >nul
)
exit /b %WINOWNER_EXIT%
