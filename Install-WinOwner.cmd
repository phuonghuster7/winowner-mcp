@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "WINOWNER_ROOT=%~dp0"
set "WINOWNER_SETUP=%WINOWNER_ROOT%scripts\Setup-WinOwner.ps1"
set "WINOWNER_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%WINOWNER_SETUP%" (
    echo [ERROR] Setup-WinOwner.ps1 is missing.
    echo Download and EXTRACT the complete WinOwner MCP ZIP before running this file.
    exit /b 2
)
if not exist "%WINOWNER_PS%" (
    echo [ERROR] Windows PowerShell 5.1 could not be found.
    exit /b 3
)
echo.
echo === WinOwner MCP v1.1.0 - Windows setup ===
echo Full Control for a verified owner; OAuth and tunnel pairing remain required.
echo.
"%WINOWNER_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%WINOWNER_SETUP%" %*
set "WINOWNER_EXIT=%ERRORLEVEL%"
echo.
if not "%WINOWNER_EXIT%"=="0" (
    echo [FAILED] Setup stopped safely with exit code %WINOWNER_EXIT%.
    echo See docs\START_HERE.vi.md for troubleshooting.
) else (
    echo [DONE] See docs\START_HERE.vi.md for ChatGPT pairing steps.
)
echo.
exit /b %WINOWNER_EXIT%
