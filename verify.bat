@echo off
setlocal
if "%~1"=="" (
    powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Mode Verify
) else (
    powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Mode Verify -InputJson "%~f1"
)
set "exit_code=%errorlevel%"
echo.
pause
exit /b %exit_code%
