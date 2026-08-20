#!/usr/bin/env pwsh
# Quick recovery when containers are up but the Windows host cannot reach published ports.
Set-Location $PSScriptRoot
Write-Host "Recovering PocketPro:NYL stack (Windows)..." -ForegroundColor Cyan
& "$PSScriptRoot\start-windows.ps1" -Recreate
exit $LASTEXITCODE