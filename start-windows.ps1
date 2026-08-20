#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Reliable Windows startup for the PocketPro:NYL Docker stack.

.DESCRIPTION
  - Binds published ports to 127.0.0.1 (see DOCKER_BIND_HOST in .env)
  - Staged compose up: chroma -> backend -> frontend
  - Verifies HTTP from the Windows host and retries on Docker port-forward glitches

.EXAMPLE
  .\start-windows.ps1
  .\start-windows.ps1 -Build
  .\start-windows.ps1 -Recreate
#>
param(
    [switch]$Build,
    [switch]$Recreate,
    [switch]$OpenBrowser,
    [int]$MaxPortWaitSec = 120
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

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

function Wait-DockerDaemon([int]$MaxSeconds = 180) {
    Write-Step "Waiting for Docker Desktop"
    $elapsed = 0
    while ($elapsed -lt $MaxSeconds) {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Docker ready (${elapsed}s)" -ForegroundColor Green
            return $true
        }
        if ($elapsed -eq 30) {
            Write-Host "  Starting Docker Desktop..." -ForegroundColor Yellow
            Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Docker daemon not reachable after ${MaxSeconds}s. Open Docker Desktop manually."
}

function Test-OfflineClientMode {
    $registry = Read-DotEnvValue "POCKETPRO_NYL_REGISTRY" ""
    $version = Read-DotEnvValue "POCKETPRO_NYL_VERSION" ""
    if ($registry -eq "pocketpro-nyl-local" -and -not [string]::IsNullOrWhiteSpace($version)) {
        return $true
    }
    $buildLocal = Read-DotEnvValue "BUILD_LOCAL" ""
    return ($buildLocal -eq "0" -and -not [string]::IsNullOrWhiteSpace($registry) -and -not [string]::IsNullOrWhiteSpace($version))
}

function Invoke-Compose([string[]]$ComposeArgs) {
    # Docker Compose logs progress to stderr; with $ErrorActionPreference=Stop that becomes a terminating error.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $allArgs = $script:ComposeFileArgs + $ComposeArgs
        $output = & docker compose @allArgs 2>&1
        foreach ($line in $output) {
            if ($line -is [System.Management.Automation.ErrorRecord]) {
                Write-Host $line.ToString()
            } else {
                Write-Host $line
            }
        }
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose failed ($LASTEXITCODE): $($allArgs -join ' ')"
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Repair-PortForwarding {
    Write-Step "Repairing Docker port forwarding (Windows)"
    Invoke-Compose @("restart", "backend", "frontend")
    Start-Sleep -Seconds 12
    Invoke-Compose @("up", "-d", "--force-recreate", "frontend")
    Start-Sleep -Seconds 10
}

$script:ComposeFileArgs = @()
$offlineClientMode = Test-OfflineClientMode
if ($offlineClientMode) {
    $script:ComposeFileArgs = @(
        "-f", "docker-compose.distribution.yml",
        "-f", "docker-compose.distribution.offline.yml",
        "-f", "docker-compose.direct.yml"
    )
    if ($Build) {
        Write-Host "Offline client mode: using pre-loaded images (skipping build)" -ForegroundColor Yellow
        $Build = $false
    }
}

$bindHost = Read-DotEnvValue "DOCKER_BIND_HOST" "127.0.0.1"
$frontendPort = [int](Read-DotEnvValue "FRONTEND_HOST_PORT" "3000")
$backendPort = [int](Read-DotEnvValue "BACKEND_HOST_PORT" "5001")
$chromaPort = [int](Read-DotEnvValue "CHROMA_HOST_PORT" "8001")

if ([string]::IsNullOrWhiteSpace($bindHost)) { $bindHost = "127.0.0.1" }

Write-Host "PocketPro:NYL Windows Start" -ForegroundColor White
if ($offlineClientMode) {
    Write-Host "  mode      : offline (pre-built images)" -ForegroundColor Cyan
}
Write-Host "  bind host : $bindHost"
Write-Host "  ports     : frontend=$frontendPort backend=$backendPort chroma=$chromaPort"
Write-Host "  app URL   : http://${bindHost}:${frontendPort}/" -ForegroundColor Green

. (Join-Path $PSScriptRoot "scripts\Wait-PocketProNYLPorts.ps1")

Wait-DockerDaemon | Out-Null

if ($Recreate) {
    Write-Step "Recreating stack"
    Invoke-Compose @("down", "--timeout", "15")
    Start-Sleep -Seconds 2
}

if ($Build) {
    Write-Step "Building images"
    $env:DOCKER_BUILDKIT = "0"
    $env:COMPOSE_DOCKER_CLI_BUILD = "0"
    Invoke-Compose @("build")
}

Write-Step "Starting services (staged)"
Invoke-Compose @("up", "-d", "--force-recreate", "chroma")
Start-Sleep -Seconds 8
Invoke-Compose @("up", "-d", "--force-recreate", "backend")
Start-Sleep -Seconds 15
Invoke-Compose @("up", "-d", "--force-recreate", "frontend")

Write-Step "Verifying host connectivity"
$result = Wait-PocketProNYLPorts -FrontendPort $frontendPort -BackendPort $backendPort -BindHost $bindHost -MaxWaitSec $MaxPortWaitSec

if (-not $result.Ok) {
    Repair-PortForwarding
    $result = Wait-PocketProNYLPorts -FrontendPort $frontendPort -BackendPort $backendPort -BindHost $bindHost -MaxWaitSec 60
}

if (-not $result.Ok) {
    Write-Host "`nPort forwarding still failing from Windows." -ForegroundColor Red
    Write-Host "Try:" -ForegroundColor Yellow
    Write-Host "  1. Restart Docker Desktop (tray icon -> Restart)"
    Write-Host "  2. Re-run: .\start-windows.ps1 -Recreate"
    Write-Host "  3. Open: http://${bindHost}:${frontendPort}/  (not localhost if IPv6 conflicts)"
    Invoke-Compose @("ps")
    exit 1
}

# NEW STEP: Trigger startup init after host connectivity is verified
Trigger-StartupInit -BackendUrl "http://${bindHost}:${backendPort}"

Write-Step "Stack healthy"
Invoke-Compose @("ps")
Write-Host "`nApp ready: $($result.FrontendUrl)" -ForegroundColor Green
Write-Host "Use http://${bindHost}:${frontendPort}/ (not localhost) if you see timeouts on Windows." -ForegroundColor Gray

if ($OpenBrowser) {
    Start-Process $result.FrontendUrl
}

exit 0