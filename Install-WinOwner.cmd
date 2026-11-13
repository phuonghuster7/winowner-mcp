@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WINOWNER_ROOT=%~dp0"
set "WINOWNER_SETUP=%WINOWNER_ROOT%scripts\Setup-WinOwner.ps1"
set "WINOWNER_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "WINOWNER_EXIT=0"

echo.
echo ===============================================
echo   WinOwner MCP v1.1.1 - CÀI ĐẶT WINDOWS
echo ===============================================
echo.
if not exist "%WINOWNER_SETUP%" (
    echo [THẤT BẠI] Chưa giải nén đầy đủ file ZIP.
    echo Vui lòng chọn "Extract All" trước khi cài.
    set "WINOWNER_EXIT=2"
    goto finish
)
if not exist "%WINOWNER_PS%" (
    echo [THẤT BẠI] Windows PowerShell không khả dụng.
    set "WINOWNER_EXIT=3"
    goto finish
)
"%WINOWNER_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%WINOWNER_SETUP%" %*
set "WINOWNER_EXIT=%ERRORLEVEL%"
echo.
if not "%WINOWNER_EXIT%"=="0" (
    echo ===============================================
    echo [THẤT BẠI] Cài đặt chưa hoàn tất.
    echo Xem WinOwner-SETUP-RESULT.txt và docs\START_HERE.vi.md.
    echo ===============================================
) else (
    echo ===============================================
    echo [THÀNH CÔNG] WinOwner MCP đã cài trên máy.
    echo Chưa kết nối ChatGPT. Xem docs\START_HERE.vi.md.
    echo Nhấp đúp Check-WinOwner.cmd để kiểm tra.
    echo ===============================================
)
:finish
if "%~1"=="" (
    echo.
    echo Nhấn phím bất kỳ để đóng cửa sổ...
    pause >nul
)
exit /b %WINOWNER_EXIT%