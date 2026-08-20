#!/usr/bin/env powershell
<#
.SYNOPSIS
  Production readiness verification script for PocketPro:NYL Project.
  Checks Docker images, environment, API endpoints, and security headers.

.DESCRIPTION
  Runs a battery of checks to confirm the application is production-ready.
  Exits with non-zero code if any critical check fails.

.PARAMETER All
  Run all checks including optional ones (default: critical only).
.PARAMETER ContainerName
  Base name for containers (default: pocketpro_nyl). # PocketPro:NYL Project
#>

param(
    [switch]$All,
    [string]$ContainerPrefix = "pocketpro_nyl"
)

$ErrorActionPreference = "Stop"
$exitCode = 0
$checksPassed = 0
$checksFailed = 0

function Read-DotEnvValue([string]$Name, [string]$Default) {
    $envPath = Join-Path (Get-Location) ".env"
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
$backendPort = [int](Read-DotEnvValue "BACKEND_HOST_PORT" "5000")
$chromaPort = [int](Read-DotEnvValue "CHROMA_HOST_PORT" "8000")

function Write-Check {
    param([string]$Message, [string]$Status, [string]$Detail = "")
    $symbol = if ($Status -eq "PASS") { "✅" } elseif ($Status -eq "FAIL") { "❌" } else { "⚠️" }
    $detailStr = if ($Detail) { " - $Detail" } else { "" }
    Write-Host "$symbol $Message$detailStr"
    if ($Status -eq "PASS") { $script:checksPassed++ }
    elseif ($Status -eq "FAIL") { $script:checksFailed++; $script:exitCode = 1 }
}

function Test-Container {
    param([string]$Name, [string]$ExpectedPort)
    $container = docker ps --filter "name=$Name" --format "{{.Names}}" | Select-Object -First 1
    if (-not $container) {
        Write-Check "Container $Name is running" "FAIL" "Container not found"
        return $false
    }
    Write-Check "Container $Name is running" "PASS" "Name: $container"

    if ($ExpectedPort) {
        $portInfo = docker port $container | Out-String
        if ($portInfo -match $ExpectedPort) {
            Write-Check "Container $Name port mapping" "PASS" "Port $ExpectedPort exposed"
        } else {
            Write-Check "Container $Name port mapping" "FAIL" "Expected port $ExpectedPort not found"
        }
    }
    return $true
}

function Test-Endpoint {
    param([string]$Name, [string]$Url, [string]$Expected = "200", [int]$TimeoutSec = 10)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        $statusCode = [int]$response.StatusCode
        if ($statusCode -eq $Expected) {
            Write-Check "Endpoint $Name" "PASS" "HTTP $statusCode"
            return $response
        } else {
            Write-Check "Endpoint $Name" "FAIL" "Expected HTTP $Expected, got $statusCode"
            return $null
        }
    } catch {
        Write-Check "Endpoint $Name" "FAIL" $_.Exception.Message
        return $null
    }
}

function Test-SecurityHeaders {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -ErrorAction Stop
        $headers = $response.Headers

        $securityChecks = @(
            @{ Name = "X-Frame-Options"; Expected = "SAMEORIGIN" }
            @{ Name = "X-Content-Type-Options"; Expected = "nosniff" }
            @{ Name = "Strict-Transport-Security"; Expected = "max-age=63072000" }
            @{ Name = "server"; ExpectedNot = "nginx" }  # server_tokens off
        )

        foreach ($check in $securityChecks) {
            $headerValue = $null
            foreach ($key in $headers.Keys) {
                if ($key -ieq $check.Name) {
                    $headerValue = $headers[$key]
                    break
                }
            }
            if ($check.ContainsKey("Expected")) {
                if ($headerValue -and $headerValue -match $check.Expected) {
                    Write-Check "Security header $($check.Name)" "PASS" "$($headerValue)"
                } else {
                    Write-Check "Security header $($check.Name)" "FAIL" "Expected '$($check.Expected)', got '$($headerValue)'"
                }
            } elseif ($check.ContainsKey("ExpectedNot")) {
                if (-not $headerValue -or $headerValue -eq "") {
                    Write-Check "Security: server version hidden" "PASS" "server header suppressed"
                } else {
                    Write-Check "Security: server version hidden" "FAIL" "Server header exposed: $headerValue"
                }
            }
        }
    } catch {
        Write-Check "Security headers check" "FAIL" $_.Exception.Message
    }
}

function Test-EnvFile {
    $envPath = Join-Path (Get-Location) ".env"
    $envExamplePath = Join-Path (Get-Location) ".env.example"
    
    if (Test-Path $envPath) {
        Write-Check ".env file exists" "PASS"
    } else {
        Write-Check ".env file exists" "FAIL" "Copy .env.example to .env and configure"
    }

    if (Test-Path $envExamplePath) {
        Write-Check ".env.example exists" "PASS"
    }
}

function Test-DockerIgnore {
    $path = Join-Path (Get-Location) ".dockerignore"
    if (Test-Path $path) {
        Write-Check ".dockerignore exists" "PASS"
    } else {
        Write-Check ".dockerignore exists" "FAIL"
    }
}

function Test-ComposeHealth {
    param([string]$Service)
    $container = docker ps --filter "name=${ContainerPrefix}_${Service}" --format "{{.Names}}" | Select-Object -First 1
    if (-not $container) { return }

    $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' $container 2>$null
    if ($health -eq "healthy") {
        Write-Check "Health check: $Service" "PASS" "Status: healthy"
    } elseif ($health -eq "N/A") {
        Write-Check "Health check: $Service" "PASS" "No healthcheck defined"
    } else {
        Write-Check "Health check: $Service" "FAIL" "Status: $health"
    }
}

# ============================================================================
# Main Checks
# ============================================================================
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "    PocketPro:NYL Production Verification" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray
Write-Host ""

# 1. Environment & Configuration
Write-Host "--- Configuration ---" -ForegroundColor Yellow
Test-EnvFile
Test-DockerIgnore

# 2. Docker Containers
Write-Host "`n--- Docker Containers ---" -ForegroundColor Yellow
Test-Container "${ContainerPrefix}_chroma" "$chromaPort"
Test-Container "${ContainerPrefix}_backend" "$backendPort"
Test-Container "${ContainerPrefix}_frontend" "$frontendPort"

# 3. Health Checks (with retry for slow-starting containers)
Write-Host "`n--- Health Checks ---" -ForegroundColor Yellow
Test-ComposeHealth "chroma"
Test-ComposeHealth "backend"

# Retry frontend health check since it may still be starting
$frontendHealthy = $false
for ($attempt = 1; $attempt -le 5; $attempt++) {
    $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' "${ContainerPrefix}_frontend" 2>$null
    if ($health -eq "healthy") {
        Write-Check "Health check: frontend" "PASS" "Status: healthy"
        $frontendHealthy = $true
        break
    }
    if ($attempt -lt 5) {
        Write-Host "  Frontend health: $health - retrying in 5s (attempt $attempt/5)..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
    }
}
if (-not $frontendHealthy) {
    Test-ComposeHealth "frontend"
}

# 4. API Endpoints
Write-Host "`n--- API Endpoints ---" -ForegroundColor Yellow
$backendUrl = "http://${bindHost}:${backendPort}"
$frontendUrl = "http://${bindHost}:${frontendPort}"

Test-Endpoint "Backend root" "${backendUrl}/api"
Test-Endpoint "Backend health" "${backendUrl}/api/health"
Test-Endpoint "Backend games" "${backendUrl}/api/games"
Test-Endpoint "ChromaDB heartbeat" "http://${bindHost}:${chromaPort}/api/v1/heartbeat" -TimeoutSec 5

# 5. Frontend
Write-Host "`n--- Frontend ---" -ForegroundColor Yellow
$frontendResponse = Test-Endpoint "Frontend root" $frontendUrl
if ($frontendResponse -and $All) {
    Test-SecurityHeaders "${frontendUrl}/api/health"
}

# 6. Docker image existence
Write-Host "`n--- Docker Images ---" -ForegroundColor Yellow
$backendImage = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -match "pocketpronyl.*backend" } | Select-Object -First 1
$frontendImage = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -match "pocketpronyl.*frontend" } | Select-Object -First 1

if ($backendImage) { Write-Check "Backend Docker image exists" "PASS" $backendImage }
else { Write-Check "Backend Docker image exists" "FAIL" "No matching image found" }

if ($frontendImage) { Write-Check "Frontend Docker image exists" "PASS" $frontendImage }
else { Write-Check "Frontend Docker image exists" "FAIL" "No matching image found" }

# 7. Summary
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "    Results: $checksPassed passed, $checksFailed failed" -ForegroundColor $(if ($checksFailed -eq 0) { "Green" } else { "Red" })
Write-Host "============================================" -ForegroundColor Cyan

if ($checksFailed -gt 0) {
    Write-Host "`nSome checks failed. Review the output above for details." -ForegroundColor Yellow
}

exit $exitCode
