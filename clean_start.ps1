#!/usr/bin/env powershell
param(
    [switch]$Build,
    [switch]$Verbose,
    [switch]$Purge
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host "  $Message" -ForegroundColor Gray
}

Set-Location $PSScriptRoot

Write-Step "Stopping compose services and removing orphans"
if ($Purge) {
    Write-Host "Purging volumes (-v)..." -ForegroundColor Yellow
    docker compose down -v --remove-orphans | Out-Null
} else {
    docker compose down --remove-orphans | Out-Null
}

Write-Step "Removing stale conflicting Mensa containers (if any)"
$stale = docker ps -a --format "{{.ID}}|{{.Names}}" |
    Where-Object {
        $_ -match "\|mensa_(backend|frontend|chroma)$" -or
        $_ -match "\|.*_mensa_(backend|frontend|chroma)$"
    }

if ($stale) {
    $ids = @()
    foreach ($line in $stale) {
        $parts = $line.Split('|', 2)
        if ($parts.Count -eq 2) {
            $ids += $parts[0]
            if ($Verbose) {
                Write-Info "Removing container: $($parts[1]) ($($parts[0]))"
            }
        }
    }
    if ($ids.Count -gt 0) {
        docker rm -f $ids | Out-Null
        Write-Info "Removed $($ids.Count) stale container(s)."
    }
} else {
    Write-Info "No stale Mensa containers found."
}

if ($Build) {
    Write-Step "Starting stack with rebuild"
    docker compose up -d --build
} else {
    Write-Step "Starting stack"
    docker compose up -d
}

Write-Step "Current compose status"
docker compose ps

$bindHost = "127.0.0.1"
$frontendPort = 3000
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*DOCKER_BIND_HOST\s*=\s*(\S+)') { $bindHost = $Matches[1] }
        if ($_ -match '^\s*FRONTEND_HOST_PORT\s*=\s*(\d+)') { $frontendPort = [int]$Matches[1] }
    }
}

Write-Host "`nDone. Open: http://${bindHost}:${frontendPort}/" -ForegroundColor Green
Write-Host "For reliable Windows startup next time, use: .\start-windows.ps1" -ForegroundColor Gray
