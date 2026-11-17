@echo off
REM Mensa Project Frontend Test Script
REM Run this to verify the frontend is working correctly

echo.
echo === MENSA PROJECT FRONTEND TEST ===
echo.

echo [1] Checking Docker Containers...
docker ps --filter "name=mensa" --format "{{.Names}}: {{.Status}}"
echo.

echo [2] Testing API Endpoints...
echo.

echo Testing /api/health...
curl -s http://localhost:3000/api/health
echo.
echo.

echo Testing /api/games...
curl -s http://localhost:3000/api/games
echo.
echo.

echo Testing /api/startup_status...
curl -s http://localhost:3000/api/startup_status
echo.
echo.

echo Testing /api/chroma/collections...
curl -s http://localhost:3000/api/chroma/collections
echo.
echo.

echo [3] Testing Frontend HTML...
curl -s http://localhost:3000 | find "<div id=\"root\">"
if %errorlevel% equ 0 (
    echo √ React app HTML loads correctly
) else (
    echo X React app HTML not found
)
echo.

echo === TEST COMPLETE ===
echo Open browser to: http://localhost:3000
echo.

pause
