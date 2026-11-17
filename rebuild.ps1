# Quick rebuild script for frontend
Write-Host "Stopping containers..."
docker compose down

Write-Host "Rebuilding frontend..."
docker compose build frontend

Write-Host "Starting all containers..."
docker compose up -d

Write-Host "Waiting for containers to be ready..."
Start-Sleep -Seconds 10

Write-Host "`nContainer status:"
docker ps --format "table {{.Names}}`t{{.Status}}"

Write-Host "`nFrontend should be available at http://localhost:3000"
