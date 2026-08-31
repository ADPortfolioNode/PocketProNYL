# PocketPro:NYL Optimized Startup Script (Windows PowerShell)
# Industry-standard startup with dynamic configuration and comprehensive monitoring

param(
    [switch]$Build,
    [switch]$Down,
    [switch]$Reset,
    [switch]$Test,
    [switch]$Monitor,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Color output helper
function Write-ColorOutput {
    param([string]$Message, [string]$Color = "White")
    $colors = @{
        "Red" = "Red"
        "Green" = "Green" 
        "Yellow" = "Yellow"
        "Blue" = "Blue"
        "Cyan" = "Cyan"
        "White" = "White"
    }
    Write-Host $Message -ForegroundColor $colors[$Color]
}

function Write-Step { Write-ColorOutput "[STEP] $args" -Color "Blue" }
function Write-Info { Write-ColorOutput "[INFO] $args" -Color "Cyan" }
function Write-Success { Write-ColorOutput "[SUCCESS] $args" -Color "Green" }
function Write-Warning { Write-ColorOutput "[WARNING] $args" -Color "Yellow" }
function Write-Error { Write-ColorOutput "[ERROR] $args" -Color "Red" }

# Configuration
$ComposeFile = Join-Path $ScriptDir "docker-compose.yml"
$Env:BACKEND_CACHE_BUSTER = if ($Env:BACKEND_CACHE_BUSTER) { $Env:BACKEND_CACHE_BUSTER } else { "stable" }
$Env:SKIP_ONNX = if ($Env:SKIP_ONNX) { $Env:SKIP_ONNX } else { "1" }

# Docker Compose command selection
function Select-ComposeCommand {
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        $script:ComposeCmd = "docker-compose -f $ComposeFile"
    } elseif (docker compose version -ErrorAction SilentlyContinue) {
        $script:ComposeCmd = "docker compose -f $ComposeFile"
    } else {
        Write-Error "Neither 'docker compose' nor 'docker-compose' found"
        exit 1
    }
    Write-Info "Using: $script:ComposeCmd"
}

# Service health check
function Wait-ServiceHealth {
    param(
        [string]$Service,
        [int]$MaxChecks = 30,
        [int]$Interval = 5
    )
    
    Write-Step "Waiting for $Service to be healthy..."
    $containerName = "pocketpro_nyl_$Service"
    
    for ($i = 1; $i -le $MaxChecks; $i++) {
        try {
            $status = docker inspect --format '{{.State.Health.Status}}' $containerName 2>$null
            if ($LASTEXITCODE -eq 0) {
                switch ($status) {
                    "healthy" {
                        Write-Success "$Service is healthy"
                        return $true
                    }
                    "unhealthy" {
                        Write-Error "$Service is unhealthy"
                        docker logs $containerName --tail 50
                        return $false
                    }
                    default {
                        Write-Info "  ... $Service status: $status ($i/$MaxChecks)"
                    }
                }
            } else {
                Write-Info "  ... $Service not ready yet ($i/$MaxChecks)"
            }
        } catch {
            Write-Info "  ... $Service check failed ($i/$MaxChecks)"
        }
        Start-Sleep -Seconds $Interval
    }
    
    Write-Error "Timeout waiting for $Service"
    return $false
}

# HTTP endpoint check
function Wait-HttpEndpoint {
    param(
        [string]$Url,
        [string]$Name,
        [int]$MaxChecks = 30,
        [int]$Interval = 3
    )
    
    Write-Step "Waiting for $Name HTTP endpoint..."
    
    for ($i = 1; $i -le $MaxChecks; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Interval -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                Write-Success "$Name HTTP endpoint is responding"
                return $true
            }
        } catch {
            Write-Info "  ... $Name not ready ($i/$MaxChecks)"
        }
        Start-Sleep -Seconds $Interval
    }
    
    Write-Error "Timeout waiting for $Name HTTP endpoint"
    return $false
}

# Prerequisites check
function Test-Prerequisites {
    Write-Step "Checking prerequisites..."
    
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error "Docker not found"
        exit 1
    }
    
    try {
        docker info | Out-Null
    } catch {
        Write-Error "Docker daemon not responding"
        exit 1
    }
    
    Write-Success "Prerequisites check passed"
}

# Environment setup
function Initialize-Environment {
    Write-Step "Setting up environment..."
    
    # Set default values
    $Env:FRONTEND_HOST_PORT = if ($Env:FRONTEND_HOST_PORT) { $Env:FRONTEND_HOST_PORT } else { "3000" }
    $Env:BACKEND_HOST_PORT = if ($Env:BACKEND_HOST_PORT) { $Env:BACKEND_HOST_PORT } else { "5001" }
    $Env:CHROMA_HOST_PORT = if ($Env:CHROMA_HOST_PORT) { $Env:CHROMA_HOST_PORT } else { "8001" }
    $Env:DOCKER_BIND_HOST = if ($Env:DOCKER_BIND_HOST) { $Env:DOCKER_BIND_HOST } else { "0.0.0.0" }
    
    Write-Success "Environment configured"
    Write-Info "  Frontend: $($Env:FRONTEND_HOST_PORT)"
    Write-Info "  Backend: $($Env:BACKEND_HOST_PORT)"
    Write-Info "  Chroma: $($Env:CHROMA_HOST_PORT)"
}

# Cleanup operations
function Invoke-Cleanup {
    Write-Step "Cleaning up existing stack..."
    
    try {
        & $script:ComposeCmd down --remove-orphans 2>$null
        docker system prune -f 2>$null
    } catch {
        Write-Warning "Cleanup encountered some issues"
    }
    
    Write-Success "Cleanup completed"
}

# Build operations
function Invoke-Build {
    Write-Step "Building Docker images..."
    
    $buildArgs = @(
        "--build-arg", "CACHE_BUSTER=$($Env:BACKEND_CACHE_BUSTER)"
        "--build-arg", "SKIP_ONNX=$($Env:SKIP_ONNX)"
    )
    
    if ($Build) {
        $buildArgs += "--no-cache"
        Write-Info "Building with --no-cache"
    }
    
    try {
        & $script:ComposeCmd build $buildArgs
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Build completed successfully"
        } else {
            Write-Error "Build failed"
            exit 1
        }
    } catch {
        Write-Error "Build failed: $_"
        exit 1
    }
}

# Start services
function Start-Services {
    Write-Step "Starting services in dependency order..."
    
    # Start Chroma
    Write-Info "Starting Chroma..."
    & $script:ComposeCmd up -d chroma
    if (-not (Wait-ServiceHealth "chroma")) { exit 1 }
    
    # Start Backend
    Write-Info "Starting Backend..."
    & $script:ComposeCmd up -d backend
    if (-not (Wait-ServiceHealth "backend")) { exit 1 }
    
    # Start Frontend
    Write-Info "Starting Frontend..."
    & $script:ComposeCmd up -d frontend
    if (-not (Wait-ServiceHealth "frontend" 90 5)) { exit 1 }
    
    Write-Success "All services started successfully"
}

# Trigger ingestion
function Invoke-IngestionTrigger {
    $backendUrl = "http://127.0.0.1:$($Env:BACKEND_HOST_PORT)"
    $forceFlag = $Force.ToString().ToLower()
    
    Write-Step "Triggering data ingestion..."
    
    $payload = "{""force"": $forceFlag}"
    try {
        $response = Invoke-WebRequest -Uri "$backendUrl/api/startup_init" `
            -Method POST `
            -ContentType "application/json" `
            -Body $payload `
            -UseBasicParsing `
            -TimeoutSec 15
        
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Write-Success "Ingestion triggered successfully (HTTP $($response.StatusCode))"
        } else {
            Write-Warning "Ingestion trigger returned HTTP $($response.StatusCode) (may be already running)"
        }
    } catch {
        Write-Error "Failed to trigger ingestion: $_"
    }
}

# Monitor ingestion
function Monitor-Ingestion {
    $backendUrl = "http://127.0.0.1:$($Env:BACKEND_HOST_PORT)"
    $maxWaitMinutes = 45
    $maxWaitSeconds = $maxWaitMinutes * 60
    $elapsed = 0
    $interval = 5
    
    Write-Step "Monitoring ingestion progress (max $maxWaitMinutes minutes)..."
    
    while ($elapsed -lt $maxWaitSeconds) {
        try {
            $statusJson = Invoke-WebRequest -Uri "$backendUrl/api/startup_status" -UseBasicParsing -TimeoutSec 10
            $status = $statusJson.Content | ConvertFrom-Json | Select-Object -ExpandProperty status
            
            switch ($status) {
                "completed" {
                    Write-Success "Ingestion completed successfully"
                    return
                }
                "error" {
                    Write-Error "Ingestion failed"
                    return
                }
                default {
                    $progress = $statusJson.Content | ConvertFrom-Json | Select-Object -ExpandProperty percent_complete
                    Write-Info "  ... Progress: $progress% | Elapsed: ${elapsed}s"
                }
            }
        } catch {
            Write-Warning "Failed to get ingestion status"
        }
        
        Start-Sleep -Seconds $interval
        $elapsed += $interval
    }
    
    Write-Warning "Ingestion monitoring timeout after $maxWaitMinutes minutes"
}

# Display status
function Show-Status {
    Write-Step "Service Status:"
    & $script:ComposeCmd ps
    
    Write-Host ""
    Write-Info "Access URLs:"
    Write-Host "  Frontend: http://127.0.0.1:$($Env:FRONTEND_HOST_PORT)"
    Write-Host "  Backend:  http://127.0.0.1:$($Env:BACKEND_HOST_PORT)/api"
    Write-Host "  Chroma:   http://127.0.0.1:$($Env:CHROMA_HOST_PORT)"
}

# Main execution
function Main {
    Write-ColorOutput "PocketPro:NYL Optimized Startup" -Color "Cyan"
    Write-Host ""
    
    # Handle reset
    if ($Reset) {
        Write-Warning "Performing full reset - this will clear all data"
        & "$ScriptDir\reset.ps1" -Yes
        exit 0
    }
    
    # Handle down
    if ($Down) {
        Write-Info "Stopping services..."
        & $script:ComposeCmd down -v
        Write-Success "Services stopped"
        exit 0
    }
    
    # Handle monitor only
    if ($Monitor) {
        Monitor-Ingestion
        exit 0
    }
    
    # Normal startup sequence
    Test-Prerequisites
    Select-ComposeCommand
    Initialize-Environment
    Invoke-Cleanup
    
    if ($Build) {
        Invoke-Build
    }
    
    Start-Services
    Invoke-IngestionTrigger
    
    if ($Test) {
        Monitor-Ingestion
        & "$ScriptDir\production_test.ps1" -WriteFile -Verbose
    } else {
        Monitor-Ingestion
    }
    
    Show-Status
    Write-Success "Startup completed successfully"
}

# Run main function
Main