@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo WinOwner installation missing.
  exit /b 1
)
echo This revokes the existing local owner bundle and linked grants only.
echo Other credentials, service profile and running operations are not changed.
choice /C YN /N /M "Revoke this owner authorization? [Y/N] "
if errorlevel 2 exit /b 2
if not errorlevel 1 exit /b 1
"%PYTHON%" "%ROOT%scripts\manage_owner_delegation.py" --oauth-db "%ROOT%runtime\secrets\oauth.db" --tasks-db "%ROOT%runtime\secrets\oauth.db" revoke-installed-owner --bundle-file "%ROOT%runtime\secrets\owner_delegation_bundle.json"
set "STATUS=%ERRORLEVEL%"
pause
exit /b %STATUS%
