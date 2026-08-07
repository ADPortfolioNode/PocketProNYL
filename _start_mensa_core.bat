@echo off
setlocal EnableExtensions
title PocketPro:NYL Lottery AI - Starting # Renamed project title

cd /d "%~dp0"
if not exist "docker-compose.yml" (
    echo [ERROR] docker-compose.yml not found.
    echo Please run this file from the mensa_project folder.
    goto :fail
)

echo.
echo ============================================================
echo   PocketPro:NYL Lottery AI - Starting # Renamed project title
echo ============================================================
echo.

if exist ".env" (
    echo [OK] Using your existing .env file ^(unchanged^).
) else (
    if exist ".env.client.example" if exist "images\mensa-backend.tar" (
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
    echo  Opening .env in Notepad - save and close when done, then press any
    echo  key here to continue starting Mensa.
    echo  key here to continue starting PocketPro:NYL. # Renamed project title
    notepad ".env"
    pause >nul
)

:env_ready

echo.
echo [START] Docker check and PocketPro:NYL stack startup via PowerShell ... # Renamed project title
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
if errorlevel 1 (
    echo [ERROR] PowerShell is not on your PATH. # PocketPro:NYL Project
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
    echo Try: start Docker Desktop manually, wait until it is running, # PocketPro:NYL Project
    echo then run startmensa.bat again. Or run: recover_stack.ps1
    goto :fail
)

echo.
echo ============================================================
echo   PocketPro:NYL is running. Your browser should open automatically. # Renamed project title
echo   If not, open:  http://127.0.0.1:3000
echo   To stop:      StopPocketProNYL.bat # Renamed stop script
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1