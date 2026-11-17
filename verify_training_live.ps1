$ErrorActionPreference = 'Stop'
$base = 'http://localhost:5000'

$games = @((Invoke-RestMethod -Uri "$base/api/games" -Method Get).games)
$checked = @()
$selected = $null
$first = $null
$second = $null

foreach ($g in $games) {
    $body = @{ game = $g } | ConvertTo-Json
    $resp = Invoke-RestMethod -Uri "$base/api/train" -Method Post -ContentType 'application/json' -Body $body

    $checked += [PSCustomObject]@{
        game = $g
        status = $resp.status
        message = $resp.message
        accuracy = $resp.accuracy
        attempts = $resp.attempts
        train_size = $resp.train_size
        validation_size = $resp.validation_size
        retained_previous_model = $resp.retained_previous_model
    }

    if ($resp.status -eq 'success' -and -not $selected) {
        $selected = $g
        $first = $resp
        Start-Sleep -Seconds 1
        $second = Invoke-RestMethod -Uri "$base/api/train" -Method Post -ContentType 'application/json' -Body $body
        break
    }
}

[PSCustomObject]@{
    selected_game = $selected
    checked_games = $checked
    first_run = $first
    second_run = $second
} | ConvertTo-Json -Depth 10
