function Wait-PocketProNYLPorts {
    param(
        [int]$FrontendPort = 3000,
        [int]$BackendPort = 5001,
        [string]$BindHost = "127.0.0.1",
        [int]$MaxWaitSec = 120,
        [int]$IntervalSec = 3
    )

    function Test-PocketProNYLHttp {
        param(
            [string]$Label,
            [string]$Url,
            [int]$TimeoutSec = 8
        )
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
            return @{ Ok = $true; Label = $Label; Status = $resp.StatusCode }
        } catch {
            return @{ Ok = $false; Label = $Label; Error = $_.Exception.Message }
        }
    }

    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    $frontendUrl = "http://${BindHost}:${FrontendPort}/api/health"
    $backendUrl = "http://${BindHost}:${BackendPort}/api/health"

    while ((Get-Date) -lt $deadline) {
        $frontend = Test-PocketProNYLHttp "frontend" $frontendUrl
        if ($frontend.Ok) {
            $startupUrl = "http://${BindHost}:${FrontendPort}/api/startup_status"
            $startup = Test-PocketProNYLHttp "startup_status" $startupUrl
            if ($startup.Ok) {
                return @{
                    Ok = $true
                    FrontendUrl = "http://${BindHost}:${FrontendPort}/"
                    BackendUrl = "http://${BindHost}:${BackendPort}/"
                    Frontend = $frontend
                    Startup = $startup
                }
            }
        }

        $backend = Test-PocketProNYLHttp "backend_direct" $backendUrl
        if ($backend.Ok -and -not $frontend.Ok) {
            Write-Host "  backend up on ${BindHost}:${BackendPort}, waiting for frontend proxy..." -ForegroundColor Yellow
        } else {
            $msg = if ($frontend.Error) { $frontend.Error } else { "waiting" }
            Write-Host "  waiting for app on ${BindHost}:${FrontendPort} ($msg)..." -ForegroundColor Gray
        }

        Start-Sleep -Seconds $IntervalSec
    }

    return @{
        Ok = $false
        FrontendUrl = "http://${BindHost}:${FrontendPort}/"
        BackendUrl = "http://${BindHost}:${BackendPort}/"
    }
}