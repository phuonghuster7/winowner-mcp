@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WINOWNER_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "WINOWNER_CHECK=%~dp0scripts\Check-WinOwner.ps1"
echo.
echo === KIỂM TRA WINOWNER MCP ===
if not exist "%WINOWNER_CHECK%" (
    echo [THẤT BẠI] Thiếu scripts\Check-WinOwner.ps1.
    set "RESULT=2"
    goto finish
)
"%WINOWNER_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%WINOWNER_CHECK%" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo [THẤT BẠI] MCP cục bộ chưa hoạt động đúng. Xem docs\START_HERE.vi.md.
) else (
    echo [THÀNH CÔNG] MCP cục bộ đã chạy và từ chối khách chưa đăng nhập.
    echo Kết nối ChatGPT là bước riêng.
)
:finish
if "%~1"=="" (
    echo.
    echo Nhấn phím bất kỳ để đóng cửa sổ...
    pause >nul
)
exit /b %RESULT%