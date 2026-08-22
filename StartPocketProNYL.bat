@echo off
setlocal EnableExtensions
title PocketPro:NYL - Starting

cd /d "%~dp0"
if not exist "docker-compose.yml" (
    echo [ERROR] docker-compose.yml not found.
    echo Please run this file from the project folder.
    goto :fail
)

echo.
echo ============================================================
echo   PocketPro:NYL - Starting
echo ============================================================
echo.

if exist ".env" (
    echo [OK] Using your existing .env file.
) else (
    if not exist ".env.example" (
        echo [ERROR] .env.example is missing. Cannot create .env.
        goto :fail
    )
    echo [SETUP] First-time install: creating .env from .env.example ...
    copy /Y ".env.example" ".env" >nul
    if errorlevel 1 (
        echo [ERROR] Could not create .env
        goto :fail
    )
    echo.
    echo  A new .env file was created. Add API keys for AI chat if you want.
    echo  Training and suggestions work without keys.
    echo.
)

echo [START] Docker check and PocketPro:NYL stack startup via PowerShell ...
if not exist "%~dp0start-windows.ps1" (
    echo [ERROR] Missing start-windows.ps1 in %~dp0
    goto :fail
)
where powershell >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PowerShell is not on your PATH.
    goto :fail
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1" -Build -OpenBrowser
if errorlevel 1 (
    echo.
    echo [ERROR] Startup did not complete successfully.
    echo Try: start Docker Desktop, wait until it is running, then retry.
    goto :fail
)

echo.
echo ============================================================
echo   PocketPro:NYL is running.
echo   Open:  http://127.0.0.1:3000
echo   Stop:  StopPocketProNYL.bat
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
