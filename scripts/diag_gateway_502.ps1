# Runtime 502 / gateway diagnostic — appends to diag_output.log
param(
    [string]$ProjectRoot = "",
    [string]$LogFile = "",
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0
)

$ErrorActionPreference = "Continue"

if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
if (-not $LogFile) {
    $LogFile = Join-Path $ProjectRoot "diag_output.log"
}

function Write-DiagLog($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    try {
        [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine)
    } catch {}
    Write-Host $line
}

function Read-HostPorts {
    param([int]$DefaultBackend = 5001, [int]$DefaultFrontend = 3000)
    $backend = $DefaultBackend
    $frontend = $DefaultFrontend
    $envPath = Join-Path $ProjectRoot ".env"
    if (Test-Path $envPath) {
        Get-Content $envPath | ForEach-Object {
            if ($_ -match '^\s*BACKEND_HOST_PORT\s*=\s*(\d+)') { $backend = [int]$Matches[1] }
            if ($_ -match '^\s*FRONTEND_HOST_PORT\s*=\s*(\d+)') { $frontend = [int]$Matches[1] }
        }
    }
    return @{ Backend = $backend; Frontend = $frontend }
}

function Test-HttpGet($label, $url, [int]$TimeoutSec = 12) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec
        $snippet = $resp.Content
        if ($snippet.Length -gt 120) { $snippet = $snippet.Substring(0, 120) }
        Write-DiagLog "PROBE OK GET $label -> $($resp.StatusCode) $snippet"
        return @{ Ok = $true; Status = $resp.StatusCode; Error = $null }
    } catch {
        Write-DiagLog "PROBE FAIL GET $label -> $($_.Exception.Message)"
        return @{ Ok = $false; Status = $null; Error = $_.Exception.Message }
    }
}

function Test-HttpPostJson($label, $url, $bodyJson, [int]$TimeoutSec = 60) {
    try {
        $resp = Invoke-RestMethod -Uri $url -Method POST -ContentType "application/json" -Body $bodyJson -TimeoutSec $TimeoutSec
        $status = if ($resp.status) { $resp.status } else { "ok" }
        Write-DiagLog "PROBE OK POST $label -> status=$status"
        return @{ Ok = $true; Status = $status; Error = $null }
    } catch {
        Write-DiagLog "PROBE FAIL POST $label -> $($_.Exception.Message)"
        return @{ Ok = $false; Status = $null; Error = $_.Exception.Message }
    }
}

function Get-ContainerState($name) {
    try {
        $id = docker ps -aq --filter "name=$name" 2>$null | Select-Object -First 1
        if (-not $id) {
            return @{ Exists = $false; Running = $false; OOMKilled = $false; Restarts = 0; Health = "missing" }
        }
        $running = docker inspect --format '{{.State.Running}}' $id 2>$null
        $oom = docker inspect --format '{{.State.OOMKilled}}' $id 2>$null
        $restarts = docker inspect --format '{{.RestartCount}}' $id 2>$null
        $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $id 2>$null
        return @{
            Exists = $true
            Running = ($running -eq "true")
            OOMKilled = ($oom -eq "true")
            Restarts = [int]($restarts -as [int])
            Health = $health
        }
    } catch {
        return @{ Exists = $false; Running = $false; OOMKilled = $false; Restarts = 0; Health = "error" }
    }
}

$ports = Read-HostPorts
if ($BackendPort -gt 0) { $ports.Backend = $BackendPort }
if ($FrontendPort -gt 0) { $ports.Frontend = $FrontendPort }

$backendBase = "http://127.0.0.1:$($ports.Backend)"
$frontendBase = "http://127.0.0.1:$($ports.Frontend)"
$predictBody = '{"game":"pick3","recent_k":5}'

Write-DiagLog ""
Write-DiagLog "--- RUNTIME GATEWAY PROBE [runtime-502-check] ---"
Write-DiagLog "Ports: backend=$($ports.Backend) frontend=$($ports.Frontend)"

Write-DiagLog "[compose ps]"
docker compose -f (Join-Path $ProjectRoot "docker-compose.yml") ps -a 2>&1 | ForEach-Object { Write-DiagLog $_ }

foreach ($svc in @("mensa_backend", "mensa_frontend", "mensa_chroma")) {
    $st = Get-ContainerState $svc
    Write-DiagLog "CONTAINER $svc running=$($st.Running) health=$($st.Health) oom=$($st.OOMKilled) restarts=$($st.Restarts)"
}

Write-DiagLog "[docker stats]"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" mensa_backend mensa_frontend mensa_chroma 2>&1 | ForEach-Object { Write-DiagLog $_ }

Write-DiagLog "[nginx error log tail]"
docker exec mensa_frontend sh -c "tail -20 /var/log/nginx/error.log 2>/dev/null || echo '(no nginx errors)'" 2>&1 | ForEach-Object { Write-DiagLog $_ }

$direct = @{
    health = Test-HttpGet "direct_health" "$backendBase/api/health" 8
    experiments = Test-HttpGet "direct_experiments" "$backendBase/api/experiments?limit=5" 15
    predict = Test-HttpPostJson "direct_predict" "$backendBase/api/predict" $predictBody 90
}
$proxy = @{
    health = Test-HttpGet "proxy_health" "$frontendBase/api/health" 8
    experiments = Test-HttpGet "proxy_experiments" "$frontendBase/api/experiments?limit=5" 15
    predict = Test-HttpPostJson "proxy_predict" "$frontendBase/api/predict" $predictBody 90
}

$directOk = ($direct.health.Ok -and $direct.experiments.Ok)
$proxyOk = ($proxy.health.Ok -and $proxy.experiments.Ok)
$backendState = Get-ContainerState "mensa_backend"

$classification = "ALL_OK"
if (-not $backendState.Running) {
    $classification = "BACKEND_DOWN"
} elseif ($directOk -and -not $proxyOk) {
    $classification = "PROXY_ONLY_FAIL"
} elseif (-not $directOk -and -not $proxyOk) {
    $classification = "BACKEND_DOWN"
} elseif ($backendState.OOMKilled -or $backendState.Restarts -gt 2) {
    $classification = "TRAIN_BUSY_HINT"
} elseif (-not $direct.predict.Ok -or -not $proxy.predict.Ok) {
    $classification = "TRAIN_BUSY_HINT"
}

Write-DiagLog "CLASSIFICATION: $classification"
 # PocketPro:NYL Project
$hints = @()
switch ($classification) {
    "BACKEND_DOWN" {
        $hints += "Backend or full stack is down. Run: .\recover_stack.ps1 then wait 90s."
        $hints += "Check: docker compose ps -a && docker logs mensa_backend --tail 50"
    }
    "PROXY_ONLY_FAIL" {
        $hints += "Direct backend works but nginx proxy fails. Rebuild frontend: docker compose up -d --build frontend"
        $hints += "Check nginx: docker exec mensa_frontend tail -30 /var/log/nginx/error.log"
    }
    "TRAIN_BUSY_HINT" {
        $hints += "Backend may be busy or recovering from training/OOM. Wait for training to finish before predict/experiments."
        if ($backendState.OOMKilled) {
            $hints += "OOMKilled=true - reduce TRAIN_MAX_ATTEMPTS or train a smaller game first."
        }
    }
    default {
        $hints += "Gateway path looks healthy. If browser still 502, hard-refresh (Ctrl+Shift+R) after recent docker compose rebuild."
    }
}

foreach ($hint in $hints) {
    Write-DiagLog "HINT: $hint"
}

Write-DiagLog "--- END RUNTIME GATEWAY PROBE ---"
Write-Host ""
Write-Host "Gateway diagnostic: $classification" -ForegroundColor $(if ($classification -eq "ALL_OK") { "Green" } else { "Yellow" })
foreach ($hint in $hints) { Write-Host "  $hint" }

if ($classification -eq "ALL_OK") { exit 0 }
if ($classification -eq "TRAIN_BUSY_HINT") { exit 2 }
exit 1