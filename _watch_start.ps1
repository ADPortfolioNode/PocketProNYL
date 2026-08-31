$log = 'C:\Users\deois\.grok\sessions\E%3A%5C2024%20RESET%5CPocketProNYL\019fd754-304a-7ed2-bc87-2c261d8040e1\terminal\call-4d01e70b-1988-46ff-96a8-447deae852a0-246.log'
$last = ''
while ($true) {
  if (Test-Path $log) {
    $tail = @(Get-Content $log -Tail 20 -ErrorAction SilentlyContinue)
    $hit = $tail | Where-Object {
      $_ -match 'SUCCESS|ERROR|Built|Building|exporting|unhealthy|FAILED|completed|Starting|healthy|TEST|production_test|Ports resolved|no-cache|WARNING|Ingestion status|STARTUP MONITORING COMPLETE'
    } | Select-Object -Last 1
    if ($hit -and $hit -ne $last) {
      Write-Output $hit
      $last = $hit
    }
    $text = ($tail -join "`n")
    if ($text -match 'STARTUP MONITORING COMPLETE|production_test|TEST SUMMARY|All tests|Build failed|ERROR: backend container|Script finished') {
      if ($text -match 'STARTUP MONITORING COMPLETE|TEST SUMMARY|Build failed|ERROR: backend|unhealthy') {
        Write-Output DONE
        exit 0
      }
    }
  }
  Start-Sleep -Seconds 25
}
