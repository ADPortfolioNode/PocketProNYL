$headers = @{'Content-Type'='application/json'}
$body = '{"game": "take5", "force": true}'
Invoke-RestMethod -Uri 'http://127.0.0.1:5001/api/ingest' -Method POST -Headers $headers -Body $body