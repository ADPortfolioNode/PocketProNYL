@echo off
<<<<<<< HEAD
setlocal EnableExtensions
title PocketProNYL - Stopping

REM ============================================================================
REM  StopPocketProNYL.bat - One-click shutdown for Windows.
REM  Double-click to stop all PocketPro:NYL Docker containers and free ports.
REM  Your data in Docker volumes is preserved for the next start.
REM ============================================================================

cd /d "%~dp0"
if not exist "docker-compose.yml" (
    echo [ERROR] docker-compose.yml not found.
    echo Please run this file from the project folder.
    goto :fail
)

echo.
echo ============================================================
echo   PocketProNYL - Stopping
echo ============================================================ # Renamed project title
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not installed or not on your PATH.
    goto :fail
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [WARN] Docker Desktop does not appear to be running.
    echo Containers may already be stopped.
    goto :done
)

echo [STOP] Shutting down PocketProNYL containers ...
docker compose down --timeout 30
if errorlevel 1 (
    echo [ERROR] docker compose down failed.
    echo Make sure Docker Desktop is running and try again.
    goto :fail
)

:done
echo.
echo [OK] PocketProNYL has been stopped.
echo      Data is saved in Docker volumes. Use the start script to run again.
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
=======
cd /d "%~dp0"
if exist "%~dp0StopMensa.bat" (
    call "%~dp0StopMensa.bat" %*
    exit /b %ERRORLEVEL%
)
docker compose -f docker-compose.yml down
exit /b %ERRORLEVEL%
>>>>>>> f8d15eea4aab2b9244b47f5221229d448e13a685
