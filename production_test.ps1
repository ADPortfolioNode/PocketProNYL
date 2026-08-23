#!/usr/bin/env powershell
# PocketPro:NYL production test suite (ASCII-safe)

param(
    [switch]$Verbose,
    [switch]$WriteFile
)

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$reportFile = ".\production_test_report_$timestamp.txt"

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

function Get-PublishedHostPort([string]$Container, [int]$ContainerPort, [int]$Fallback) {
    try {
        $raw = docker port $Container "$ContainerPort" 2>$null
        if ($raw) {
            $line = ($raw | Select-Object -First 1).ToString().Trim()
            if ($line -match ':(\d+)\s*$') {
                return [int]$Matches[1]
            }
        }
    } catch { }
    return $Fallback
}

$bindHost = "127.0.0.1"
$frontendPortEnv = [int](Read-DotEnvValue "FRONTEND_HOST_PORT" "3000")
$backendPortEnv = [int](Read-DotEnvValue "BACKEND_HOST_PORT" "5001")
$chromaPortEnv = [int](Read-DotEnvValue "CHROMA_HOST_PORT" "8001")
$frontendPort = Get-PublishedHostPort "pocketpro_nyl_frontend" 80 $frontendPortEnv
$backendPort = Get-PublishedHostPort "pocketpro_nyl_backend" 5000 $backendPortEnv
$chromaPort = Get-PublishedHostPort "pocketpro_nyl_chroma" 8000 $chromaPortEnv
$frontendBase = "http://${bindHost}:${frontendPort}"
$backendBase = "http://${bindHost}:${backendPort}"

function Write-Report {
    param([string]$Message, [string]$Color = "White")
    Write-Host $Message -ForegroundColor $Color
    if ($WriteFile) { Add-Content -Path $reportFile -Value $Message }
}

function Test-Endpoint {
    param([string]$Url, [string]$Description, [int]$TimeoutSec = 10, [switch]$Optional)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Report "  [PASS] $Description (200 OK)" -Color Green
            return @{ status = "pass"; code = 200; body = $response.Content }
        }
        $label = if ($Optional) { "WARN" } else { "FAIL" }
        $color = if ($Optional) { "Yellow" } else { "Red" }
        Write-Report "  [$label] $Description (Status: $($response.StatusCode))" -Color $color
        return @{ status = $(if ($Optional) { "warn" } else { "fail" }) }
    } catch {
        $label = if ($Optional) { "WARN" } else { "FAIL" }
        $color = if ($Optional) { "Yellow" } else { "Red" }
        Write-Report "  [$label] $Description (Error: $($_.Exception.Message))" -Color $color
        return @{ status = $(if ($Optional) { "warn" } else { "fail" }) }
    }
}

Write-Report "`n============================================================" -Color Cyan
Write-Report "POCKETPRO:NYL PRODUCTION TEST SUITE" -Color Cyan
Write-Report "Build: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Color Cyan
Write-Report "Frontend: $frontendBase  (.env=$frontendPortEnv, published=$frontendPort)" -Color Cyan
Write-Report "Backend:  $backendBase  (.env=$backendPortEnv, published=$backendPort)" -Color Cyan
Write-Report "Chroma:   http://${bindHost}:${chromaPort}  (.env=$chromaPortEnv, published=$chromaPort)" -Color Cyan
Write-Report "============================================================`n" -Color Cyan

Write-Report "[1] CONTAINER HEALTH CHECK" -Color Yellow
Write-Report "------------------------------------------------------------"
$containerCount = 0
$healthyCount = 0
$containerLines = docker ps --no-trunc --filter "name=pocketpro_nyl"
$containerTargets = @(
    @{ name = "pocketpro_nyl_frontend"; healthyKeyword = "Up" }
    @{ name = "pocketpro_nyl_backend"; healthyKeyword = "healthy|Up" }
    @{ name = "pocketpro_nyl_chroma"; healthyKeyword = "Up" }
)
foreach ($target in $containerTargets) {
    $line = $containerLines | Where-Object { $_ -match $target.name } | Select-Object -First 1
    $containerCount++
    if ($line -and $line -match $target.healthyKeyword) {
        Write-Report "  [PASS] $($target.name) running" -Color Green
        $healthyCount++
    } else {
        Write-Report "  [FAIL] $($target.name) not running" -Color Red
    }
}
Write-Report "Summary: $healthyCount/$containerCount containers healthy`n" -Color Cyan

Write-Report "[2] FRONTEND HTML VALIDATION" -Color Yellow
Write-Report "------------------------------------------------------------"
$htmlResult = Test-Endpoint "$frontendBase" "Frontend HTML"
if ($htmlResult.status -eq "pass" -and $htmlResult.body -match 'id="root"') {
    Write-Report "  [PASS] React root element present" -Color Green
}
Write-Report ""

Write-Report "[3] CRITICAL API ENDPOINTS" -Color Yellow
Write-Report "------------------------------------------------------------"
$endpoints = @(
    @{ url = "$frontendBase/api/health"; desc = "/api/health (proxy)"; optional = $false }
    @{ url = "$frontendBase/api/games"; desc = "/api/games (proxy)"; optional = $false }
    @{ url = "$frontendBase/api/startup_status"; desc = "/api/startup_status (proxy)"; optional = $false }
    @{ url = "$frontendBase/api/chroma/collections"; desc = "/api/chroma/collections (proxy)"; optional = $false }
    @{ url = "$frontendBase/api/experiments"; desc = "/api/experiments (proxy)"; optional = $false }
    @{ url = "$backendBase/api/health"; desc = "/api/health (direct host)"; optional = $true }
    @{ url = "$backendBase/api/train_settings?game=pick3"; desc = "/api/train_settings (direct host)"; optional = $true; timeout = 20 }
)
$apiPassCount = 0; $apiRequired = 0; $apiRequiredPass = 0
foreach ($endpoint in $endpoints) {
    $timeoutSec = if ($endpoint.ContainsKey('timeout')) { [int]$endpoint.timeout } else { 10 }
    $optional = [bool]$endpoint.optional
    if (-not $optional) { $apiRequired++ }
    $result = Test-Endpoint $endpoint.url $endpoint.desc $timeoutSec -Optional:$optional
    if ($result.status -eq "pass") {
        $apiPassCount++
        if (-not $optional) { $apiRequiredPass++ }
    }
}
Write-Report "Summary: $apiPassCount/$($endpoints.Count) endpoints responding (required proxy $apiRequiredPass/$apiRequired)`n" -Color Cyan

Write-Report "[4] RESPONSE CONTRACT CHECKS" -Color Yellow
Write-Report "------------------------------------------------------------"
try {
    $gamesResp = Invoke-WebRequest -Uri "$frontendBase/api/games" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $gamesPayload = $null
    try { $gamesPayload = $gamesResp.Content | ConvertFrom-Json } catch { }
    $games = @()
    if ($gamesPayload -is [System.Array]) { $games = $gamesPayload }
    elseif ($gamesPayload -and $gamesPayload.PSObject.Properties.Name -contains "games") { $games = @($gamesPayload.games) }
    $expectedGames = @("take5", "pick3", "powerball", "megamillions", "pick10", "cash4life", "quickdraw", "nylotto")
    $foundCount = @($expectedGames | Where-Object { $games -contains $_ }).Count
    Write-Report "  Games endpoint coverage: $foundCount/8" -Color $(if ($foundCount -eq 8) { "Green" } else { "Yellow" })
} catch {
    Write-Report "  [FAIL] Could not fetch /api/games: $($_.Exception.Message)" -Color Red
}
try {
    $status = (Invoke-WebRequest -Uri "$frontendBase/api/startup_status" -UseBasicParsing -TimeoutSec 10).Content | ConvertFrom-Json
    Write-Report "  Startup status: $($status.status)" -Color Green
} catch {
    Write-Report "  [FAIL] Could not fetch /api/startup_status: $($_.Exception.Message)" -Color Red
}
Write-Report ""

Write-Report "[5] REGRESSION CHECKS" -Color Yellow
Write-Report "------------------------------------------------------------"
try {
    $htmlResp = Invoke-WebRequest -Uri "$frontendBase" -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
    if ($htmlResp.Content -match '/api/api') { Write-Report "  [FAIL] Double /api path found" -Color Red }
    else { Write-Report "  [PASS] No double /api path found" -Color Green }
} catch {
    Write-Report "  [FAIL] Could not evaluate frontend HTML" -Color Red
}
Write-Report ""

Write-Report "[6] CONNECTIVITY" -Color Yellow
Write-Report "------------------------------------------------------------"
@(
    @{ host = $bindHost; port = $frontendPort; service = "Frontend (Nginx)"; optional = $false }
    @{ host = $bindHost; port = $backendPort; service = "Backend host port"; optional = $true }
    @{ host = $bindHost; port = $chromaPort; service = "ChromaDB"; optional = $true }
) | ForEach-Object {
    $isReachable = Test-NetConnection -ComputerName $_.host -Port $_.port -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($isReachable) {
        Write-Report "  [PASS] $($_.service) reachable on $($_.host):$($_.port)" -Color Green
    } elseif ($_.optional) {
        Write-Report "  [WARN] $($_.service) not reachable on $($_.host):$($_.port)" -Color Yellow
    } else {
        Write-Report "  [FAIL] $($_.service) not reachable on $($_.host):$($_.port)" -Color Red
    }
}

Write-Report "`n============================================================" -Color Cyan
Write-Report "TEST SUMMARY" -Color Cyan
Write-Report "Containers healthy: $healthyCount/$containerCount" -Color Cyan
Write-Report "Required proxy APIs: $apiRequiredPass/$apiRequired" -Color Cyan
Write-Report "Ready URL: $frontendBase" -Color Green
Write-Report "Direct backend URL: $backendBase" -Color Cyan
if ($WriteFile) { Write-Host "`nReport saved to: $reportFile" -ForegroundColor Cyan }
