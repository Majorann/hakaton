@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Mode Setup
set "exit_code=%errorlevel%"
echo.
pause
exit /b %exit_code%
