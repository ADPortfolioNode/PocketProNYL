#!/usr/bin/env pwsh
# PocketPro:NYL Project - Ingestion Progress Monitor (PowerShell version)
# Real-time display of game data ingestion

param(
    [int]$RefreshInterval = 2,
    [string]$ApiBase = "http://127.0.0.1:5000"
)

$StatusEndpoint = "$ApiBase/api/startup_status"
$startTime = Get-Date

function Format-Time {
    param([double]$Seconds)
    if ($Seconds -lt 60) {
        return "{0:F1}s" -f $Seconds
    } elseif ($Seconds -lt 3600) {
        return "{0:F1}m" -f ($Seconds / 60)
    } else {
        return "{0:F1}h" -f ($Seconds / 3600)
    }
}

function Print-ProgressBar {
    param(
        [int]$Progress,
        [int]$Total,
        [int]$Width = 40
    )
    
    if ($Total -eq 0) {
        $percentage = 0
        $filled = 0
    } else {
        $percentage = ($Progress / $Total) * 100
        $filled = [Math]::Floor(($Progress / $Total) * $Width)
    }
    
    $bar = ('█' * $filled) + ('░' * ($Width - $filled))
    return "[{0}] {1}/{2} ({3:F1}%)" -f $bar, $Progress, $Total, $percentage
}

function Print-Status {
    param($StatusData)
    
    $elapsed = (Get-Date) - $startTime
    $elapsedStr = Format-Time $elapsed.TotalSeconds
    
    $totalGames = $StatusData.total
    $progress = $StatusData.progress
    $currentGame = $StatusData.current_game
    $currentTask = $StatusData.current_task
    $overallStatus = $StatusData.status
    $games = $StatusData.games
    
    # Calculate stats
    $completed = ($games.PSObject.Properties | Where-Object { $_.Value -eq 'completed' }).Count
    $failed = ($games.PSObject.Properties | Where-Object { $_.Value -like '*failed*' }).Count
    $pending = $totalGames - $completed - $failed
    
    # Print header
    Write-Host "`n" -NoNewline
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host "POCKETPRO:NYL - INGESTION PROGRESS MONITOR" -ForegroundColor Cyan -NoNewline
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host ""
    
    # Print status
    if ($overallStatus -eq 'completed') {
        Write-Host "Status: " -NoNewline -ForegroundColor White
        Write-Host "COMPLETE ✓" -ForegroundColor Green
    } elseif ($overallStatus -eq 'ingesting') {
        Write-Host "Status: " -NoNewline -ForegroundColor White
        Write-Host "INGESTING..." -ForegroundColor Yellow
    } else {
        Write-Host "Status: " -NoNewline -ForegroundColor White
        Write-Host $overallStatus.ToUpper() -ForegroundColor Blue
    }
    
    Write-Host "Elapsed: $elapsedStr" -ForegroundColor White
    Write-Host ""
    
    # Print progress bar
    Write-Host "Overall Progress:" -ForegroundColor White
    $progressBar = Print-ProgressBar $progress $totalGames
    Write-Host $progressBar -ForegroundColor Yellow
    Write-Host ""
    
    # Print stats
    Write-Host "Game Stats:" -ForegroundColor White
    Write-Host "  ✓ Completed: $completed/$totalGames" -ForegroundColor Green
    Write-Host "  ⟳ Pending:   $pending/$totalGames" -ForegroundColor Yellow
    if ($failed -gt 0) {
        Write-Host "  ✗ Failed:    $failed/$totalGames" -ForegroundColor Red
    }
    Write-Host ""
    
    # Print current game
    if ($currentGame) {
        Write-Host "Currently Processing:" -ForegroundColor White
        Write-Host "  Game: $($currentGame.ToUpper())" -ForegroundColor Yellow
        if ($currentTask) {
            Write-Host "  Task: $currentTask" -ForegroundColor Gray
        }
        Write-Host ""
    }
    
    # Print game-by-game status
    if ($games) {
        Write-Host "Game Status Details:" -ForegroundColor White
        $games.PSObject.Properties | Sort-Object Name | ForEach-Object {
            $gameName = $_.Name
            $gameStatus = $_.Value
            
            if ($gameStatus -eq 'completed') {
                $symbol = "✓"
                $color = "Green"
            } elseif ($gameStatus -eq 'pending') {
                $symbol = "⟳"
                $color = "Yellow"
            } elseif ($gameStatus -like '*failed*') {
                $symbol = "✗"
                $color = "Red"
            } else {
                $symbol = "?"
                $color = "Gray"
            }
            
            if ($gameName -eq $currentGame) {
                Write-Host "  $symbol " -NoNewline -ForegroundColor $color
                Write-Host $gameName.ToUpper() -ForegroundColor Yellow -NoNewline
                Write-Host " ($gameStatus)" -ForegroundColor Gray
            } else {
                Write-Host "  $symbol $($gameName.ToUpper())" -ForegroundColor $color
            }
        }
    }
}

# Main monitoring loop
Write-Host "Connecting to API: $StatusEndpoint" -ForegroundColor Cyan
Write-Host ""

$retryCount = 0
$maxRetries = 30

while ($true) {
    try {
        $response = Invoke-RestMethod -Uri $StatusEndpoint -TimeoutSec 5 -ErrorAction Stop
        $retryCount = 0
        
        Clear-Host
        Print-Status $response
        
        # Check if complete
        if ($response.status -eq 'completed') {
            $elapsed = (Get-Date) - $startTime
            Write-Host ""
            Write-Host "✓ Ingestion Complete!" -ForegroundColor Green
            Write-Host "  Total time: $(Format-Time $elapsed.TotalSeconds)" -ForegroundColor Green
            Write-Host "  Games ingested: $($response.progress)/$($response.total)" -ForegroundColor Green
            Write-Host ""
            break
        }
        
        Start-Sleep -Seconds $RefreshInterval
    } catch [System.Net.WebException] {
        $retryCount++
        Clear-Host
        Write-Host "`n" -NoNewline
        Write-Host ("=" * 70) -ForegroundColor Cyan
        Write-Host "WAITING FOR API..." -ForegroundColor Yellow
        Write-Host ("=" * 70) -ForegroundColor Cyan
        Write-Host ""
        Write-Host "⚠ Waiting for API to be ready ($retryCount/$maxRetries)..." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Make sure services are running:" -ForegroundColor White
        Write-Host "  docker-compose ps" -ForegroundColor Gray
        Write-Host "  docker-compose up -d --build" -ForegroundColor Gray
        
        if ($retryCount -ge $maxRetries) {
            Write-Host ""
            Write-Host "✗ Could not connect to API after $($maxRetries * 2) seconds" -ForegroundColor Red
            break
        }
        
        Start-Sleep -Seconds $RefreshInterval
    } catch {
        Clear-Host
        Write-Host "✗ Error: $($_.Exception.Message)" -ForegroundColor Red
        Start-Sleep -Seconds $RefreshInterval
    }
}
