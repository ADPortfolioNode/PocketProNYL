@echo off
cd /d "%~dp0"
if exist "%~dp0StopMensa.bat" (
    call "%~dp0StopMensa.bat" %*
    exit /b %ERRORLEVEL%
)
docker compose -f docker-compose.yml down
exit /b %ERRORLEVEL%
