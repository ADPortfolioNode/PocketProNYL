#!/usr/bin/env powershell
# MENSA PROJECT PRODUCTION TEST SUITE (ASCII-safe)

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

$bindHost = Read-DotEnvValue "DOCKER_BIND_HOST" "127.0.0.1"
if ([string]::IsNullOrWhiteSpace($bindHost)) { $bindHost = "127.0.0.1" }
$frontendPort = [int](Read-DotEnvValue "FRONTEND_HOST_PORT" "3000")
$backendPort = [int](Read-DotEnvValue "BACKEND_HOST_PORT" "5000")
$chromaPort = [int](Read-DotEnvValue "CHROMA_HOST_PORT" "8000")
$frontendBase = "http://${bindHost}:${frontendPort}"
$backendBase = "http://${bindHost}:${backendPort}"

function Write-Report {
    param([string]$Message, [string]$Color = "White")
    Write-Host $Message -ForegroundColor $Color
    if ($WriteFile) {
        Add-Content -Path $reportFile -Value $Message
    }
}

function Test-Endpoint {
    param([string]$Url, [string]$Description, [int]$TimeoutSec = 10)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Report "  [PASS] $Description (200 OK)" -Color Green
            return @{ status = "pass"; code = 200; body = $response.Content }
        }

        Write-Report "  [FAIL] $Description (Status: $($response.StatusCode))" -Color Red
        return @{ status = "fail"; code = $response.StatusCode; body = $response.Content }
    }
    catch {
        Write-Report "  [FAIL] $Description (Error: $($_.Exception.Message))" -Color Red
        return @{ status = "fail"; error = $_.Exception.Message }
    }
}

Write-Report "`n============================================================" -Color Cyan
Write-Report "POCKETPRO:NYL PRODUCTION TEST SUITE" -Color Cyan
Write-Report "Build: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Color Cyan
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
    if ($line) {
        if ($line -match $target.healthyKeyword) {
            Write-Report "  [PASS] $($target.name) running" -Color Green
            $healthyCount++
        } else {
            Write-Report "  [FAIL] $($target.name) not healthy" -Color Red
        }
    } else {
        Write-Report "  [FAIL] $($target.name) missing" -Color Red
    }
}

Write-Report "Summary: $healthyCount/$containerCount containers healthy`n" -Color Cyan

Write-Report "[2] FRONTEND HTML VALIDATION" -Color Yellow
Write-Report "------------------------------------------------------------"
$htmlResult = Test-Endpoint "$frontendBase" "Frontend HTML"
if ($htmlResult.status -eq "pass") {
    if ($htmlResult.body -match 'id="root"') {
        Write-Report "  [PASS] React root element present" -Color Green
    } else {
        Write-Report "  [FAIL] React root element missing" -Color Red
    }
}
Write-Report ""

Write-Report "[3] CRITICAL API ENDPOINTS" -Color Yellow
Write-Report "------------------------------------------------------------"
$endpoints = @(
    @{ url = "$frontendBase/api/health"; desc = "/api/health (proxy)" }
    @{ url = "$frontendBase/api/games"; desc = "/api/games (proxy)" }
    @{ url = "$frontendBase/api/startup_status"; desc = "/api/startup_status (proxy)" }
    @{ url = "$frontendBase/api/chroma/collections"; desc = "/api/chroma/collections (proxy)" }
    @{ url = "$frontendBase/api/experiments"; desc = "/api/experiments (proxy)" }
    @{ url = "$backendBase/api/health"; desc = "/api/health (direct)" }
    @{ url = "$backendBase/api/train_settings?game=pick3"; desc = "/api/train_settings (direct)"; timeout = 20 }
)

$apiPassCount = 0
foreach ($endpoint in $endpoints) {
    $timeoutSec = if ($endpoint.ContainsKey('timeout')) { [int]$endpoint.timeout } else { 10 }
    $result = Test-Endpoint $endpoint.url $endpoint.desc $timeoutSec
    if ($result.status -eq "pass") {
        $apiPassCount++
    }
}
Write-Report "Summary: $apiPassCount/$($endpoints.Count) endpoints responding`n" -Color Cyan

Write-Report "[4] RESPONSE CONTRACT CHECKS" -Color Yellow
Write-Report "------------------------------------------------------------"

try {
    $gamesResp = Invoke-WebRequest -Uri "$frontendBase/api/games" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $gamesPayload = $null
    try { $gamesPayload = $gamesResp.Content | ConvertFrom-Json } catch { }
    if ($null -eq $gamesPayload) {
        Write-Report "  [FAIL] /api/games returned non-JSON: $($gamesResp.Content.Substring(0,[Math]::Min(120,$gamesResp.Content.Length)))" -Color Red
        $games = @()
    } elseif ($gamesPayload -is [System.Array]) {
        $games = $gamesPayload
    } elseif ($gamesPayload.PSObject.Properties.Name -contains "games") {
        $games = @($gamesPayload.games)
    } else {
        Write-Report "  [WARN] Unexpected /api/games shape: $($gamesResp.Content.Substring(0,[Math]::Min(120,$gamesResp.Content.Length)))" -Color Yellow
        $games = @()
    }
    $expectedGames = @("take5", "pick3", "powerball", "megamillions", "pick10", "cash4life", "quickdraw", "nylotto")
    $foundCount = 0
    foreach ($game in $expectedGames) {
        if ($games -contains $game) { $foundCount++ }
    }
    Write-Report "  Games endpoint coverage: $foundCount/8" -Color $(if ($foundCount -eq 8) { "Green" } else { "Yellow" })
} catch {
    Write-Report "  [FAIL] Could not fetch /api/games: $($_.Exception.Message)" -Color Red
}

try {
    $statusResp = Invoke-WebRequest -Uri "$frontendBase/api/startup_status" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $status = $null
    try { $status = $statusResp.Content | ConvertFrom-Json } catch { }
    if ($null -eq $status) {
        Write-Report "  [FAIL] /api/startup_status returned non-JSON: $($statusResp.Content.Substring(0,[Math]::Min(120,$statusResp.Content.Length)))" -Color Red
    } else {
        $requiredFields = @("status", "progress", "total", "games")
        $fieldCount = 0
        foreach ($field in $requiredFields) {
            if ($status.PSObject.Properties.Name -contains $field) { $fieldCount++ }
        }
        Write-Report "  Startup status fields: $fieldCount/$($requiredFields.Count)  [ingestion status: $($status.status)]" -Color $(if ($fieldCount -eq 4) { "Green" } else { "Yellow" })
    }
} catch {
    Write-Report "  [FAIL] Could not fetch /api/startup_status: $($_.Exception.Message)" -Color Red
}
Write-Report ""

Write-Report "[5] REGRESSION CHECKS" -Color Yellow
Write-Report "------------------------------------------------------------"
try {
    $htmlResp = Invoke-WebRequest -Uri "$frontendBase" -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
    if ($htmlResp.Content -match '/api/api') {
        Write-Report "  [FAIL] Double /api path found" -Color Red
    } else {
        Write-Report "  [PASS] No double /api path found" -Color Green
    }
} catch {
    Write-Report "  [FAIL] Could not evaluate frontend HTML for regressions" -Color Red
}
Write-Report ""

Write-Report "[6] CONNECTIVITY" -Color Yellow
Write-Report "------------------------------------------------------------"
@(
    @{ host = $bindHost; port = $frontendPort; service = "Frontend (Nginx)" }
    @{ host = $bindHost; port = $backendPort; service = "Backend (FastAPI)" }
    @{ host = $bindHost; port = $chromaPort; service = "ChromaDB" }
) | ForEach-Object {
    $isReachable = Test-NetConnection -ComputerName $_.host -Port $_.port -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($isReachable) {
        Write-Report "  [PASS] $($_.service) reachable on $($_.host):$($_.port)" -Color Green
    } else {
        Write-Report "  [FAIL] $($_.service) not reachable on $($_.host):$($_.port)" -Color Red
    }
}

Write-Report "`n============================================================" -Color Cyan
Write-Report "TEST SUMMARY" -Color Cyan
Write-Report "============================================================" -Color Cyan
Write-Report "Containers healthy: $healthyCount/$containerCount" -Color Cyan
Write-Report "Critical API endpoints responding: $apiPassCount/$($endpoints.Count)" -Color Cyan
Write-Report "Ready URL: $frontendBase" -Color Green
Write-Report "Backend URL: $backendBase" -Color Green

if ($WriteFile) {
    Write-Host "`nReport saved to: $reportFile" -ForegroundColor Cyan
}

Write-Report "" -Color White
