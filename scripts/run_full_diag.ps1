# Full PocketPro:NYL stack diagnostic + recovery (Windows PowerShell)
$ErrorActionPreference = "Continue"
$ProjectRoot = "E:\2024 RESET\PocketProNYL"
$LogFile = Join-Path $ProjectRoot "diag_output.log"
$ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"

function Write-Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    try {
        [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine)
    } catch {}
    Write-Host $line
}

function Get-ComposeCmd() {
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return @("docker", "compose") }
    return @("docker-compose")
}

function Wait-DockerDaemon([int]$MaxSeconds = 120) {
    Write-Log "Waiting for Docker daemon (max ${MaxSeconds}s)..."
    $elapsed = 0
    while ($elapsed -lt $MaxSeconds) {
        docker version 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Docker daemon ready (${elapsed}s)."
            return $true
        }
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    Write-Log "FAIL: Docker daemon not reachable after ${MaxSeconds}s."
    return $false
}

function Invoke-HttpCheck($label, $url, [int]$TimeoutSec = 10) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec
        $snippet = $resp.Content
        if ($snippet.Length -gt 100) { $snippet = $snippet.Substring(0, 100) }
        Write-Log "OK $label -> $($resp.StatusCode) $snippet"
        return $true
    } catch {
        Write-Log "FAIL $label -> $($_.Exception.Message)"
        return $false
    }
}

$BackendPort = 5001
$FrontendPort = 3000
$ChromaPort = 8001
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*BACKEND_HOST_PORT\s*=\s*(\d+)') { $BackendPort = [int]$Matches[1] }
        if ($_ -match '^\s*FRONTEND_HOST_PORT\s*=\s*(\d+)') { $FrontendPort = [int]$Matches[1] }
        if ($_ -match '^\s*CHROMA_HOST_PORT\s*=\s*(\d+)') { $ChromaPort = [int]$Matches[1] }
    }
}

$useComposeV2 = $false
docker compose version 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $useComposeV2 = $true }

Write-Log ""
Write-Log "--- PowerShell Full Diagnostic Run ---"
Write-Log "Compose: $(if ($useComposeV2) { 'docker compose' } else { 'docker-compose' })"
Write-Log "Ports: backend=$BackendPort frontend=$FrontendPort chroma=$ChromaPort"

if (-not (Wait-DockerDaemon 120)) {
    Write-Log "HINT: Restart Docker Desktop, wait for Running, then re-run this script."
    exit 1
}

function Run-Compose([string[]]$Args) {
    if ($useComposeV2) {
        docker compose @Args
    } else {
        docker-compose @Args
    }
}

Write-Log "compose ps (before):"
Run-Compose @("-f", $ComposeFile, "ps", "-a") 2>&1 | ForEach-Object { Write-Log $_ }

Write-Log "compose up -d --force-recreate..."
Run-Compose @("-f", $ComposeFile, "up", "-d", "--force-recreate") 2>&1 | ForEach-Object { Write-Log $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAIL: compose up exited $LASTEXITCODE"
    exit 1
}

Write-Log "Waiting 40s for health..."
Start-Sleep -Seconds 40

Write-Log "compose ps (after):"
Run-Compose @("-f", $ComposeFile, "ps", "-a") 2>&1 | ForEach-Object { Write-Log $_ }

$checks = @(
    @("backend", "http://127.0.0.1:${BackendPort}/api/health"),
    @("frontend", "http://127.0.0.1:${FrontendPort}/api/health"),
    @("train_settings", "http://127.0.0.1:${BackendPort}/api/train_settings?game=pick3"),
    @("experiments", "http://127.0.0.1:${BackendPort}/api/experiments?limit=3")
)
$passed = 0
$totalChecks = $checks.Count + 1
foreach ($c in $checks) {
    if (Invoke-HttpCheck $c[0] $c[1] 12) { $passed++ }
}

Write-Log "backend logs (tail 20):"
docker logs pocketpro_nyl_backend --tail 20 2>&1 | ForEach-Object { Write-Log $_ }

# Quick predict probe (pick3 is fast)
try {
    $predictBody = '{"game":"pick3","recent_k":5}'
    $predictResp = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/predict" -Method POST -ContentType "application/json" -Body $predictBody -TimeoutSec 90
    Write-Log "OK predict_direct -> status=$($predictResp.status)"
    $passed++
} catch {
    Write-Log "FAIL predict_direct -> $($_.Exception.Message)"
}

Write-Log "SUMMARY: $passed / $totalChecks checks passed"

$gatewayScript = Join-Path $ProjectRoot "scripts\diag_gateway_502.ps1"
if (Test-Path $gatewayScript) {
    Write-Log "Running runtime gateway probe..."
    & $gatewayScript -ProjectRoot $ProjectRoot -LogFile $LogFile -BackendPort $BackendPort -FrontendPort $FrontendPort
    $gatewayExit = $LASTEXITCODE
    Write-Log "Gateway probe exit code: $gatewayExit"
}

if ($passed -eq $totalChecks) {
    Write-Log "DIAGNOSTIC PASS"
    exit 0
}
Write-Log "DIAGNOSTIC PARTIAL/FAIL"
exit 1