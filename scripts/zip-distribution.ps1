#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [string]$PackageDir
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $PackageDir)) {
    throw "Package directory not found: $PackageDir"
}

$label = Split-Path $PackageDir -Leaf
$out = Split-Path $PackageDir -Parent
$appArchive = Join-Path $out "${label}_app.tar.gz"
$imagesArchive = Join-Path $out "${label}_images.tar.gz"
$fullArchive = Join-Path $out "${label}.tar.gz"

if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
    throw "tar.exe not found. Use Windows 10+ or install bsdtar."
}

function Invoke-TarGz([string]$Destination, [string[]]$TarArgs) {
    if (Test-Path $Destination) {
        Remove-Item -Force $Destination
    }
    & tar @TarArgs
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed creating $Destination"
    }
}

Write-Host "Creating app archive (source, no Docker tars)..."
$stage = Join-Path $env:TEMP "pocketpro_nyl_tar_$label"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null

Get-ChildItem -Path $PackageDir | ForEach-Object {
    if ($_.Name -eq "images") {
        $imgStage = Join-Path $stage "images"
        New-Item -ItemType Directory -Path $imgStage | Out-Null
        Get-ChildItem -Path $_.FullName -File | Where-Object { $_.Extension -ne ".tar" } | ForEach-Object {
            Copy-Item $_.FullName -Destination (Join-Path $imgStage $_.Name)
        }
    } else {
        Copy-Item -Path $_.FullName -Destination (Join-Path $stage $_.Name) -Recurse -Force
    }
}

Invoke-TarGz -Destination $appArchive -TarArgs @("-czf", $appArchive, "-C", $stage, ".")

$tars = Get-ChildItem -Path (Join-Path $PackageDir "images") -Filter "*.tar" -ErrorAction SilentlyContinue
if ($tars) {
    Write-Host "Creating images archive..."
    $imgDir = Join-Path $PackageDir "images"
    $tarArgs = @("-czf", $imagesArchive, "-C", $imgDir)
    $tarArgs += ($tars | ForEach-Object { $_.Name })
    Invoke-TarGz -Destination $imagesArchive -TarArgs $tarArgs
}

Write-Host "Creating full archive (optional single file)..."
Invoke-TarGz -Destination $fullArchive -TarArgs @("-czf", $fullArchive, "-C", $out, $label)

Remove-Item -Recurse -Force $stage

Get-Item $appArchive, $imagesArchive, $fullArchive -ErrorAction SilentlyContinue | ForEach-Object {
    $mb = [math]::Round($_.Length / 1MB, 1)
    Write-Host "$($_.Name) ($mb MB)"
}