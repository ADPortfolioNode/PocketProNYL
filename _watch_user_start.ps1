# Watch for a NEW startup_monitor_*.log from the user's bash ./start.sh --build --test
$root = 'E:\2024 RESET\PocketProNYL'
$baseline = Get-ChildItem (Join-Path $root 'startup_monitor_*.log') -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$baselineName = if ($baseline) { $baseline.Name } else { '' }
$baselineTime = if ($baseline) { $baseline.LastWriteTime } else { [datetime]'2000-01-01' }
Write-Output "WATCHING for new startup_monitor after $baselineName ($baselineTime)"

$activeLog = $null
$last = ''
while ($true) {
  $logs = @(Get-ChildItem (Join-Path $root 'startup_monitor_*.log') -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
  $newest = $logs | Select-Object -First 1
  if ($newest -and ($newest.Name -ne $baselineName -or $newest.LastWriteTime -gt $baselineTime.AddSeconds(5))) {
    if (-not $activeLog -or $activeLog -ne $newest.FullName) {
      $activeLog = $newest.FullName
      Write-Output "DETECTED $($newest.Name)"
    }
  }

  # Also track growing baseline if user reuses same session somehow
  if (-not $activeLog -and $newest -and $newest.Length -gt 600 -and $newest.LastWriteTime -gt (Get-Date).AddMinutes(-2)) {
    $activeLog = $newest.FullName
    Write-Output "TRACKING $($newest.Name)"
  }

  if ($activeLog -and (Test-Path $activeLog)) {
    $tail = @(Get-Content $activeLog -Tail 25 -ErrorAction SilentlyContinue)
    $hit = $tail | Where-Object {
      $_ -match 'SUCCESS|ERROR|WARNING|Built|Building|exporting|Starting|healthy|Ingestion status|STARTUP MONITORING COMPLETE|production_test|TEST SUMMARY|Ports resolved|Failed|unhealthy'
    } | Select-Object -Last 1
    if ($hit -and $hit -ne $last) {
      Write-Output $hit
      $last = $hit
    }
    $text = $tail -join "`n"
    if ($text -match 'STARTUP MONITORING COMPLETE|TEST SUMMARY') {
      Write-Output DONE
      exit 0
    }
    if ($text -match 'Build failed|ERROR: Failed to start|ERROR: Backend health') {
      Write-Output FAILED
      exit 1
    }
  }
  Start-Sleep -Seconds 20
}
