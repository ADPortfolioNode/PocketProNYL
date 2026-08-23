# Recreate all PocketPro containers so Docker Desktop refreshes Windows port forwards.
# Usage: .\scripts\rebind_host_ports.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "Rebinding PocketPro:NYL host ports (force-recreate all services)..." -ForegroundColor Cyan
docker compose up -d --force-recreate chroma backend frontend
if ($LASTEXITCODE -ne 0) { throw "docker compose recreate failed" }

Start-Sleep -Seconds 8
Write-Host "`nPublished ports:" -ForegroundColor Cyan
docker port pocketpro_nyl_frontend 80
docker port pocketpro_nyl_backend 5000
docker port pocketpro_nyl_chroma 8000
docker compose ps
