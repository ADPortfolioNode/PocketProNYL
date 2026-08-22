@echo off
cd /d "%~dp0"
if exist "%~dp0start-windows.ps1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1" %*
    exit /b %ERRORLEVEL%
)
if exist "%~dp0_start_mensa_core.bat" (
    call "%~dp0_start_mensa_core.bat" %*
    exit /b %ERRORLEVEL%
)
echo [ERROR] Missing start-windows.ps1 in %~dp0
pause
exit /b 1
