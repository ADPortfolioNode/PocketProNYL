$headers = @{'Content-Type'='application/json'}
$body = '{"force": true}'
Invoke-RestMethod -Uri 'http://127.0.0.1:5001/api/startup_init' -Method POST -Headers $headers -Body $body