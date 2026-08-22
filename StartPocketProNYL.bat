@echo off
<<<<<<< HEAD
setlocal EnableExtensions
title PocketPro:NYL - Starting

cd /d "%~dp0"
if not exist "docker-compose.yml" (
    echo [ERROR] docker-compose.yml not found.
    echo Please run this file from the project folder.
    goto :fail
)

echo.
echo ============================================================ # Renamed project title
echo   PocketPro:NYL - Starting
echo ============================================================
echo.

if exist ".env" (
    echo [OK] Using your existing .env file ^(unchanged^).
) else (
    if exist ".env.client.example" if exist "images\pocketpro-nyl-backend.tar" (
        echo [SETUP] Client package: creating .env from .env.client.example ...
        copy /Y ".env.client.example" ".env" >nul
        goto :env_ready
    )
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
    echo  A new .env file was created. Add API keys for AI chat if you want
    echo  the concierge features. Training and suggestions work without keys.
    echo.
    echo  Opening .env in Notepad - save and close when done, then press any key here to continue.
    notepad ".env"
    pause >nul
)

:env_ready

echo.
echo [START] Docker check and PocketPro:NYL stack startup via PowerShell ...
if exist ".env" (
    findstr /B /C:"POCKETPRO_NYL_REGISTRY=pocketpro-nyl-local" ".env" >nul 2>&1 # Renamed registry variable
    if not errorlevel 1 (
        echo         ^(offline client package — uses pre-loaded images^)
    ) else (
        echo         ^(first dev build may take 10-20 minutes^)
    )
) else (
    echo         ^(first run may take 10-20 minutes^)
)
echo.

if not exist "%~dp0start-windows.ps1" (
    echo [ERROR] Missing start-windows.ps1 in %~dp0
    goto :fail
)
where powershell >nul 2>&1
if errorlevel 1 ( # PocketPro:NYL Project
    echo [ERROR] PowerShell is not on your PATH.
    goto :fail
)
set "START_ARGS=-OpenBrowser"
if exist ".env" (
    findstr /B /C:"POCKETPRO_NYL_REGISTRY=pocketpro-nyl-local" ".env" >nul 2>&1
    if errorlevel 1 set "START_ARGS=-Build -OpenBrowser"
) else (
    set "START_ARGS=-Build -OpenBrowser"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1" %START_ARGS%
if errorlevel 1 (
    echo.
    echo [ERROR] Startup did not complete successfully.
    echo Try: start Docker Desktop manually, wait until it is running,
    echo then run this script again. Or run: recover_stack.ps1
    goto :fail
)

echo.
echo ============================================================
echo   PocketPro:NYL is running. Your browser should open automatically.
echo   If not, open:  http://127.0.0.1:3000
echo   To stop:      StopPocketProNYL.bat
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
=======
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
>>>>>>> f8d15eea4aab2b9244b47f5221229d448e13a685
