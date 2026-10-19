@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose.ps1"
set "DIAG_EXIT=%ERRORLEVEL%"
echo.
echo Diagnostics finished. Reports stay in diagnostic-* beside this file.
pause
exit /b %DIAG_EXIT%
