$ErrorActionPreference = 'Stop'
$base = 'http://localhost:5000'
$outFile = 'verify_train_predict_strategy_report.json'

$gamesResp = Invoke-RestMethod -Uri "$base/api/games" -Method Get
$games = @($gamesResp.games)

$results = @()
$selectedGame = $null
$train1 = $null
$train2 = $null
$predict = $null

foreach ($g in $games) {
    $trainBody = @{ game = $g } | ConvertTo-Json
    $trainResp = Invoke-RestMethod -Uri "$base/api/train" -Method Post -ContentType 'application/json' -Body $trainBody

    $results += [PSCustomObject]@{
        game = $g
        train_status = $trainResp.status
        train_message = $trainResp.message
        accuracy = $trainResp.accuracy
        candidate_accuracy = $trainResp.candidate_accuracy
        previous_accuracy = $trainResp.previous_accuracy
        used_previous_training = $trainResp.used_previous_training
        model_strategy = $trainResp.model_strategy
        blend_weight = $trainResp.blend_weight
        retained_previous_model = $trainResp.retained_previous_model
    }

    if ($trainResp.status -eq 'COMPLETED' -or $trainResp.status -eq 'success') {
        $selectedGame = $g
        $train1 = $trainResp
        Start-Sleep -Seconds 1
        $train2 = Invoke-RestMethod -Uri "$base/api/train" -Method Post -ContentType 'application/json' -Body $trainBody

        $predictBody = @{ game = $g; recent_k = 10 } | ConvertTo-Json
        $predict = Invoke-RestMethod -Uri "$base/api/predict" -Method Post -ContentType 'application/json' -Body $predictBody
        break
    }
}

$report = [PSCustomObject]@{
    selected_game = $selectedGame
    scan_results = $results
    train_run_1 = $train1
    train_run_2 = $train2
    predict_result = $predict
}

$report | ConvertTo-Json -Depth 15 | Out-File -FilePath $outFile -Encoding utf8
Write-Output ("REPORT_WRITTEN=" + $outFile)
