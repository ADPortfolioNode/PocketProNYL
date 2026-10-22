#!/usr/bin/env pwsh
# Quick fix script for backend connection issues

Write-Host "🔧 Mensa Project - Quick Fix Script" -ForegroundColor Cyan
Write-Host ""

# Step 1: Stop all containers
Write-Host "1. Stopping all containers..." -ForegroundColor Yellow
docker compose down
Start-Sleep -Seconds 2

# Step 2: Rebuild backend
Write-Host ""
Write-Host "2. Rebuilding backend container..." -ForegroundColor Yellow
docker compose build --no-cache backend
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Backend build failed!" -ForegroundColor Red
    exit 1
}

# Step 3: Start services
Write-Host ""
Write-Host "3. Starting services..." -ForegroundColor Yellow
docker compose up -d

# Step 4: Wait for services
Write-Host ""
Write-Host "4. Waiting for services to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Step 5: Check status
Write-Host ""
Write-Host "5. Checking service status..." -ForegroundColor Yellow
docker compose ps

# Step 6: Test backend
Write-Host ""
Write-Host "6. Testing backend connectivity..." -ForegroundColor Yellow
Start-Sleep -Seconds 3
try {
    $response = Invoke-WebRequest -Uri "http://localhost:5000/api/health" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "✅ Backend is responding!" -ForegroundColor Green
    Write-Host "Response: $($response.Content)" -ForegroundColor Green
} catch {
    Write-Host "❌ Backend is NOT responding" -ForegroundColor Red
    Write-Host ""
    Write-Host "Backend logs:" -ForegroundColor Yellow
    docker compose logs backend --tail 50
    exit 1
}

# Step 7: Show access URLs
Write-Host ""
Write-Host "=" * 50 -ForegroundColor Green
Write-Host "✅ Services are running!" -ForegroundColor Green
Write-Host "=" * 50 -ForegroundColor Green
Write-Host ""
Write-Host "Frontend: http://localhost:3000" -ForegroundColor Cyan
Write-Host "Backend:  http://localhost:5000/api" -ForegroundColor Cyan
Write-Host "ChromaDB: http://localhost:8000" -ForegroundColor Cyan
Write-Host ""
Write-Host "To view logs: docker compose logs -f" -ForegroundColor Yellow
