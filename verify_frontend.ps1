#!/usr/bin/env pwsh
# Frontend Verification Script (ASCII-safe)

function Read-DotEnvValue([string]$Name, [string]$Default) {
    $envPath = Join-Path $PSScriptRoot ".env"
    if (-not (Test-Path $envPath)) { return $Default }
    foreach ($line in Get-Content $envPath) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.+?)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $Default
}

$bindHost = Read-DotEnvValue "DOCKER_BIND_HOST" "127.0.0.1"
if ([string]::IsNullOrWhiteSpace($bindHost)) { $bindHost = "127.0.0.1" }
$frontendPort = [int](Read-DotEnvValue "FRONTEND_HOST_PORT" "3000")
$frontendBase = "http://${bindHost}:${frontendPort}"

Write-Host "`n=== POCKETPRO:NYL FRONTEND VERIFICATION ===" -ForegroundColor Cyan
Write-Host "Target: $frontendBase" -ForegroundColor Gray

Write-Host "`n[1] Checking containers..." -ForegroundColor Yellow
$containers = docker ps --filter "name=pocketpro_nyl" --format "{{.Names}}:{{.Status}}"
foreach ($c in $containers) {
    if ($c -match "unhealthy") {
        Write-Host "    [WARN] $c" -ForegroundColor Yellow
    } else {
        Write-Host "    [OK] $c" -ForegroundColor Green
    }
}

function Test-FrontendEndpoint {
    param([string]$Label, [string]$Url, [scriptblock]$OnSuccess)
    Write-Host "`n$Label" -ForegroundColor Yellow
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            & $OnSuccess $response
        } else {
            Write-Host "    [FAIL] HTTP $($response.StatusCode)" -ForegroundColor Red
        }
    } catch {
        Write-Host "    [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

Test-FrontendEndpoint "[2] /api/health" "$frontendBase/api/health" {
    param($response)
    $body = $response.Content | ConvertFrom-Json
    Write-Host "    [OK] Health: $($body.status)" -ForegroundColor Green
}

Test-FrontendEndpoint "[3] /api/games" "$frontendBase/api/games" {
    param($response)
    $body = $response.Content | ConvertFrom-Json
    $games = @($body.games)
    Write-Host "    [OK] Games loaded: $($games.Count)" -ForegroundColor Green
    $games | ForEach-Object { Write-Host "       - $_" }
}

Test-FrontendEndpoint "[4] /api/startup_status" "$frontendBase/api/startup_status" {
    param($response)
    $body = $response.Content | ConvertFrom-Json
    Write-Host "    [OK] Status: $($body.status) ($($body.progress)/$($body.total))" -ForegroundColor Green
}

Test-FrontendEndpoint "[5] /api/chroma/collections" "$frontendBase/api/chroma/collections" {
    param($response)
    $body = $response.Content | ConvertFrom-Json
    $count = @($body.collections).Count
    Write-Host "    [OK] Collections: $count" -ForegroundColor Green
}

Test-FrontendEndpoint "[6] Frontend HTML" "$frontendBase" {
    param($response)
    if ($response.Content -match 'id="root"') {
        Write-Host "    [OK] React root element present" -ForegroundColor Green
    } else {
        Write-Host "    [WARN] HTML loads but React root not found" -ForegroundColor Yellow
    }
}

Write-Host "`n[7] Data volume writable..." -ForegroundColor Yellow
docker exec pocketpro_nyl_backend sh -c "touch /data/experiments/permission_test.tmp && rm /data/experiments/permission_test.tmp" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "    [OK] /data/experiments is writable" -ForegroundColor Green
} else {
    Write-Host "    [FAIL] Cannot write to /data/experiments" -ForegroundColor Red
}

Write-Host "`n=== VERIFICATION COMPLETE ===" -ForegroundColor Cyan
Write-Host "Open browser: $frontendBase" -ForegroundColor Green