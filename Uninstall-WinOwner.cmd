@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WINOWNER_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "WINOWNER_BOOTSTRAP=%~dp0scripts\Bootstrap-WindowsMcp.ps1"
echo.
echo === GỠ DỊCH VỤ WINOWNER MCP ===
echo Chỉ dừng dịch vụ và bỏ tự khởi động. Không xóa file hay khóa riêng.
if not exist "%WINOWNER_BOOTSTRAP%" (
    echo [THẤT BẠI] Thiếu file bộ gỡ cài đặt.
    set "RESULT=2"
    goto finish
)
"%WINOWNER_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%WINOWNER_BOOTSTRAP%" -Remove %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo [THẤT BẠI] Không thể gỡ dịch vụ an toàn. Xem docs\ADVANCED_CHATGPT.vi.md.
) else (
    echo [THÀNH CÔNG] Dịch vụ cục bộ đã dừng và bỏ tự khởi động.
    echo Để ngừng quyền truy cập ChatGPT, hãy ngắt kết nối trong ChatGPT.
    echo Dữ liệu và khóa trên máy được giữ lại cho chủ máy.
    >"%~dp0WinOwner-SETUP-RESULT.txt" echo WinOwner MCP local service removed. Secrets preserved.
)
:finish
if "%~1"=="" (
    echo.
    echo Nhấn phím bất kỳ để đóng cửa sổ...
    pause >nul
)
exit /b %RESULT%