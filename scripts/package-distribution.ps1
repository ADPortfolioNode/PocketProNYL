#Requires -Version 5.1
<#
.SYNOPSIS
  Package Mensa for client delivery (zip-ready folder).

.DESCRIPTION
  Creates release/mensa_client_<version>/ with source, docs, launchers, and
  optional pre-built Docker images (mensa-local/mensa-backend + frontend).

.EXAMPLE
  .\scripts\package-distribution.ps1
  .\scripts\package-distribution.ps1 -Version 1.0.0 -IncludeChromaImage
  .\scripts\package-distribution.ps1 -SkipBuild -IncludeImages:$false
#>
param(
    [string]$Version = "",
    [string]$OutputRoot = "release",
    [switch]$IncludeImages = $true,
    [switch]$IncludeChromaImage,
    [switch]$SkipBuild,
    [switch]$CreateZip = $true
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $Version) {
    $Version = (Get-Date -Format "yyyyMMdd")
}

$PackageName = "mensa_client_$Version"
$PackageDir = Join-Path $OutputRoot $PackageName
$RegistryTag = "mensa-local"
$BackendImage = "${RegistryTag}/mensa-backend:${Version}"
$FrontendImage = "${RegistryTag}/mensa-frontend:${Version}"
$ChromaImage = "chromadb/chroma:0.5.3"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-DockerReady {
    docker version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not available. Start Docker Desktop and retry."
    }
}

function Ensure-BuiltImages {
    if ($SkipBuild) {
        return
    }

    Write-Step "Building backend and frontend images"
    docker compose -f docker-compose.yml build backend frontend
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose build failed"
    }
}

function Export-DockerImages {
    param([string]$DestDir)

    Write-Step "Tagging and exporting Docker images"
    $backendBuilt = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -match "mensa_project-backend" } | Select-Object -First 1
    $frontendBuilt = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -match "mensa_project-frontend" } | Select-Object -First 1 # PocketPro:NYL Project
 # PocketPro:NYL Project
    if (-not $backendBuilt -or -not $frontendBuilt) {
        if ($SkipBuild) {
            throw "Built images not found. Run without -SkipBuild or build manually first."
        }
        throw "Could not locate mensa_project-backend / mensa_project-frontend images after build."
    }

    docker tag $backendBuilt $BackendImage
    docker tag $frontendBuilt $FrontendImage
    if ($LASTEXITCODE -ne 0) { throw "docker tag failed" }

    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

    docker save $BackendImage -o (Join-Path $DestDir "mensa-backend.tar")
    if ($LASTEXITCODE -ne 0) { throw "docker save backend failed" }

    docker save $FrontendImage -o (Join-Path $DestDir "mensa-frontend.tar")
    if ($LASTEXITCODE -ne 0) { throw "docker save frontend failed" }

    if ($IncludeChromaImage) {
        Write-Host "Pulling Chroma image for offline bundle..."
        docker pull $ChromaImage
        docker save $ChromaImage -o (Join-Path $DestDir "chroma.tar")
        if ($LASTEXITCODE -ne 0) { throw "docker save chroma failed" }
    }

    @"
# Load pre-built Mensa images (offline install)
# Windows: .\images\load-images.ps1
# Linux / Mac: chmod +x images/load-images.sh then ./images/load-images.sh

MENSA_REGISTRY=$RegistryTag
MENSA_VERSION=$Version
"@ | Set-Content -Path (Join-Path $DestDir "images.env") -Encoding UTF8

    @"
#Requires -Version 5.1
`$ErrorActionPreference = "Stop"
`$here = Split-Path -Parent `$MyInvocation.MyCommand.Path
Write-Host "Loading Mensa Docker images..."
docker load -i "`$here\mensa-backend.tar"
docker load -i "`$here\mensa-frontend.tar"
if (Test-Path "`$here\chroma.tar") {
    docker load -i "`$here\chroma.tar"
}
Write-Host "Done. Images tagged as mensa-local/mensa-*:$Version"
"@ | Set-Content -Path (Join-Path $DestDir "load-images.ps1") -Encoding UTF8

    $loadSh = @'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Loading Mensa Docker images..."
docker load -i "$HERE/mensa-backend.tar"
docker load -i "$HERE/mensa-frontend.tar"
if [[ -f "$HERE/chroma.tar" ]]; then
  docker load -i "$HERE/chroma.tar"
fi
echo "Done. Images tagged as mensa-local/mensa-*:VERSION_TAG"
'@ -replace 'VERSION_TAG', $Version
    Set-Content -Path (Join-Path $DestDir "load-images.sh") -Value $loadSh -Encoding UTF8
}

function Copy-PackageFiles {
    param([string]$Dest)

    Write-Step "Copying application files to $Dest"

    if (Test-Path $Dest) {
        Remove-Item -Recurse -Force $Dest
    }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null

    $excludeDirs = @(
        ".git", "node_modules", "frontend\node_modules", "frontend\build",
        "__pycache__", ".venv", "venv", "env", "mcps", "terminals", "agent-tools",
        ".cursor", "data", "release", "dist", "Removed", "Using", ".snapshots"
    ) | ForEach-Object { $_.ToLowerInvariant() }

    $excludeFiles = @(
        ".env", ".env.local", ".env.production", ".env.production.local"
    ) | ForEach-Object { $_.ToLowerInvariant() }

    $includeRoots = @(
        "backend", "frontend", "deploy", "scripts", "docs"
    )

    $excludeScriptNames = @("package-distribution.ps1", "zip-distribution.ps1")

    $includeFiles = @(
        "README.md",
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.direct.yml",
        "docker-compose.distribution.yml",
        "docker-compose.distribution.build.yml",
        "docker-compose.distribution.offline.yml",
        ".env.example",
        ".env.production.example",
        "StartMensa.bat",
        "Start Mensa.bat",
        "StopMensa.bat",
        "Stop Mensa.bat",
        "_start_mensa_core.bat",
        "start-windows.ps1",
        "recover_stack.ps1",
        "rebuild.ps1",
        "production_test.ps1",
        "verify_frontend.ps1",
        "verify_production.ps1",
        "test_all_workflows.py",
        "verify_training_learning.py"
    )

    function Should-SkipPath([string]$RelativePath) {
        $norm = $RelativePath.Replace("/", "\").ToLowerInvariant()
        foreach ($dir in $excludeDirs) {
            if ($norm -eq $dir -or $norm.StartsWith("$dir\") -or $norm.Contains("\$dir\")) {
                return $true
            }
        }
        $leaf = Split-Path $norm -Leaf
        if ($excludeFiles -contains $leaf) { return $true }
        if ($leaf -match '\.(log|pyc|pyo|bak|zip|tar|tar\.gz|tgz)$') { return $true }
        if ($excludeScriptNames -contains $leaf) { return $true }
        if ($leaf -match '^(diag_|startup_|training_live|all_games_run|train_|verify_.*_live)') { return $true }
        return $false
    }

    foreach ($item in $includeRoots) {
        if (-not (Test-Path $item)) { continue }
        Get-ChildItem -Path $item -Recurse -Force | ForEach-Object {
            $rel = $_.FullName.Substring($Root.Length).TrimStart("\", "/")
            if (Should-SkipPath $rel) { return }
            $target = Join-Path $Dest $rel
            if ($_.PSIsContainer) {
                New-Item -ItemType Directory -Force -Path $target | Out-Null
            } else {
                $parent = Split-Path $target -Parent
                if (-not (Test-Path $parent)) {
                    New-Item -ItemType Directory -Force -Path $parent | Out-Null
                }
                Copy-Item -Path $_.FullName -Destination $target -Force
            }
        }
    }

    foreach ($file in $includeFiles) {
        if (Test-Path $file) {
            $target = Join-Path $Dest $file
            $parent = Split-Path $target -Parent
            if ($parent -and -not (Test-Path $parent)) {
                New-Item -ItemType Directory -Force -Path $parent | Out-Null
            }
            Copy-Item -Path $file -Destination $target -Force
        }
    }
}

function Write-InstallGuide {
    param([string]$Dest)

    $builtAt = Get-Date -Format "yyyy-MM-dd HH:mm"
    $chromaNote = if ($IncludeChromaImage) {
        "Chroma is included (images/chroma.tar)."
    } else {
        "Chroma is pulled from Docker Hub on first start (chromadb/chroma:0.5.3)."
    }

    $winStep3 = if ($IncludeImages) {
        "Run images\load-images.ps1 once (PowerShell). StartMensa.bat creates .env from .env.client.example automatically."
    } else {
        "Double-click StartMensa.bat (creates .env on first run)."
    }

    $imageSteps = if ($IncludeImages) {
        @(
            "### Load pre-built images (recommended)",
            "",
            "**Windows (PowerShell):**",
            "    .\images\load-images.ps1",
            "    copy .env.client.example .env",
            "",
            "**Linux / Mac:**",
            "    chmod +x images/load-images.sh",
            "    ./images/load-images.sh",
            "    cp .env.client.example .env"
        ) -join "`n"
    } else {
        @(
            "### Build images on first run",
            "",
            "Images are built from source on the client machine (10-20 minutes first time).",
            "",
            "**Windows:** double-click StartMensa.bat",
            "",
            "**Linux / Mac:**",
            "    cp .env.example .env",
            "    docker compose up --build -d"
        ) -join "`n"
    }

    $imagesLine = if ($IncludeImages) {
        "- images/ - pre-built Docker images (offline)"
    } else {
        ""
    }

    $archiveName = "mensa_client_$Version"

    $installLines = @(
        "# PocketPro:NYL - Client Installation Guide",
        "",
        "## If you received a .tar.gz archive",
        "",
        "Extract first, then open this file inside the extracted folder:",
        "",
        "    tar -xzf ${archiveName}.tar.gz",
        "    cd $PackageName",
        "",
        "(Windows: PowerShell, Windows 10+ tar, or 7-Zip.)",
        "",
        "**Package version:** $Version",
        "**Built:** $builtAt",
        "",
        "## Requirements",
        "",
        "| Item | Minimum |",
        "|------|---------|",
        "| RAM | 8 GB |",
        "| Disk | 20 GB free (more if ingesting all games) |",
        "| Docker | Docker Desktop (Windows/Mac) or Docker Engine + Compose v2 (Linux) |",
        "| Network | Internet for first Chroma pull (unless chroma.tar included) |",
        "",
        $chromaNote,
        "",
        "---",
        "",
        "## Option A - Windows desktop (easiest)",
        "",
        "1. Install Docker Desktop and wait until it shows Running.",
        "2. Unzip this folder anywhere (e.g. C:\Mensa).",
        "3. $winStep3",
        "4. Double-click StartMensa.bat.",
        "5. Open http://127.0.0.1:3000 when the startup window shows Stack healthy.",
        "6. Use StopMensa.bat when finished (your data is kept).",
        "",
        "**Tips:** Use 127.0.0.1 not localhost if you see timeouts. Hard-refresh with Ctrl+Shift+R after updates.",
        "",
        "---",
        "",
        "## Option B - Linux / Mac local",
        "",
        $imageSteps,
        "",
        "    docker compose -f docker-compose.distribution.yml -f docker-compose.distribution.offline.yml -f docker-compose.direct.yml up -d",
        "",
        "Open http://127.0.0.1:3000",
        "",
        "---",
        "",
        "## Option C - Public web server (HTTPS)",
        "",
        "1. Copy .env.production.example to .env and set DOMAIN, ACME_EMAIL,",
        "   MENSA_REGISTRY=mensa-local and MENSA_VERSION=$Version (if using bundled images).",
        "2. Load images: ./images/load-images.sh",
        "3. Deploy: chmod +x scripts/deploy-production.sh && ./scripts/deploy-production.sh",
        "4. App will be at https://your-domain",
        "",
        "Subscriber login (optional): see docs/deployment/PUBLIC_DISTRIBUTION.md",
        "",
        "---",
        "",
        "## API keys (optional)",
        "",
        "Training, ingestion, and suggestions work without API keys.",
        "Add keys to .env only if you want AI chat (GEMINI, OPENAI, GROK).",
        "",
        "---",
        "",
        "## Typical workflow",
        "",
        "1. Ingest - load draw history for a game.",
        "2. Train - build a model (experiments save accuracy + settings).",
        "3. Suggest - get next-draw numbers (use Suggest All Games for every game).",
        "4. Chat (optional) - RAG concierge when API keys are set.",
        "",
        "---",
        "",
        "## Troubleshooting",
        "",
        "| Issue | Fix |",
        "|-------|-----|",
        "| 502 / gateway errors | Wait 90s after start; run scripts\diag_gateway_502.ps1 (Windows) |",
        "| Training timeout | Lower target accuracy, max attempts, N estimators in dashboard |",
        "| Port in use | Edit FRONTEND_HOST_PORT, BACKEND_HOST_PORT in .env |",
        "| Docker not running | Start Docker Desktop and retry |",
        "",
        "More: docs/guides/TROUBLESHOOTING.md",
        "",
        "---",
        "",
        "## Package contents",
        "",
        "- backend/, frontend/ - application source",
        "- docker-compose*.yml - stack definitions",
        "- StartMensa.bat / StopMensa.bat - Windows launchers",
        "- scripts/deploy-production.sh - Linux server deploy",
        "- docs/ - deployment and operations guides",
        $imagesLine,
        "",
        "---",
        "",
        "## Support checklist",
        "",
        "Windows: .\verify_frontend.ps1 and .\production_test.ps1",
        "Python: python test_all_workflows.py"
    ) | Where-Object { $_ -ne "" -or $_ -eq "" }

    ($installLines | Where-Object { $null -ne $_ }) -join "`n" | Set-Content -Path (Join-Path $Dest "INSTALL.md") -Encoding UTF8

    @"
PocketPro:NYL Client Distribution
Version: $Version
Registry: $RegistryTag
Images included: $(if ($IncludeImages) { "yes" } else { "no (build from source)" })
Chroma offline: $(if ($IncludeChromaImage) { "yes" } else { "no" })
"@ | Set-Content -Path (Join-Path $Dest "VERSION.txt") -Encoding UTF8

    @"
# Client .env for pre-built images (copy to .env)
# Windows local: works with StartMensa.bat after loading images
# Server: use with docker-compose.distribution.offline.yml

MENSA_REGISTRY=$RegistryTag
MENSA_VERSION=$Version
BUILD_LOCAL=0

DOCKER_BIND_HOST=127.0.0.1
FRONTEND_HOST_PORT=3000
BACKEND_HOST_PORT=5001
CHROMA_HOST_PORT=8001

GEMINI_API_KEY=
OPENAI_API_KEY=
CHAT_GPT_API_KEY=
GROK_API_KEY=
GROK_API_BASE=https://api.x.ai/v1
GROK_MODEL=grok-3-mini-beta
"@ | Set-Content -Path (Join-Path $Dest ".env.client.example") -Encoding UTF8
}

function New-ClientReadme {
    param([string]$Dest)

    $readme = @(
        "# PocketPro:NYL Predictive RAG - Client Package",
        "",
        "Lottery ingestion, training, suggestions, and optional AI chat.",
        "",
        "Start here: read INSTALL.md for step-by-step setup.",
        "",
        "| Platform | Quick start |",
        "|----------|-------------|",
        "| Windows | StartMensa.bat |",
        "| Linux server | scripts/deploy-production.sh |",
        "| Offline images | images/load-images.ps1 or images/load-images.sh |",
        "",
        "Version: $Version"
    ) -join "`n"
    Set-Content -Path (Join-Path $Dest "README.md") -Value $readme -Encoding UTF8
}

function New-OfflineCompose {
    param([string]$Dest)

    @"
# Use pre-loaded mensa-local images (no GHCR pull, no local build).
# Pair with docker-compose.distribution.yml and .env.client.example # PocketPro:NYL Project
#
#   docker compose -f docker-compose.distribution.yml -f docker-compose.distribution.offline.yml -f docker-compose.direct.yml up -d
# PocketPro:NYL Project
services:
  backend:
    image: `${MENSA_REGISTRY:-mensa-local}/mensa-backend:`${MENSA_VERSION:-latest}
    pull_policy: never

  frontend:
    image: `${MENSA_REGISTRY:-mensa-local}/mensa-frontend:`${MENSA_VERSION:-latest}
    pull_policy: never
"@ | Set-Content -Path (Join-Path $Dest "docker-compose.distribution.offline.yml") -Encoding UTF8
}

function New-ZipArchive {
    param(
        [string]$SourceDir,
        [string]$ZipPath,
        [string]$PackageLabel
    )

    Write-Step "Creating distribution archives (tar.gz)"
    $zipScript = Join-Path $PSScriptRoot "zip-distribution.ps1"
    if (-not (Test-Path $zipScript)) {
        throw "Missing $zipScript"
    }
    & $zipScript -PackageDir $SourceDir
    if ($LASTEXITCODE -ne 0) {
        throw "zip-distribution.ps1 failed"
    }
}

function Write-SendToClientNote {
    param([string]$OutRoot, [string]$Label)

    $note = @(
        "Mensa client delivery - $Version",
        "==================================",
        "",
        "READY TO SEND",
        "-------------",
        "",
        "Option 1 - Single file (recommended)",
        "  ${Label}.tar.gz",
        "  Full package: source, INSTALL.md, launchers, Docker images.",
        "",
        "Option 2 - Split downloads",
        "  ${Label}_app.tar.gz    - source + docs (small)",
        "  ${Label}_images.tar.gz - pre-built Docker images",
        "",
        "WINDOWS CLIENT (after extract)",
        "------------------------------",
        "1. Install Docker Desktop",
        "2. images\load-images.ps1",
        "3. StartMensa.bat  (auto-creates .env from .env.client.example)",
        "4. http://127.0.0.1:3000",
        "",
        "REBUILD (for you)",
        "-----------------",
        "  .\scripts\package-distribution.ps1 -Version $Version",
        "  .\scripts\zip-distribution.ps1 -PackageDir release\$Label",
        "",
        "Do NOT send your .env file (contains API keys)."
    ) -join "`n"
    Set-Content -Path (Join-Path $OutRoot "SEND_TO_CLIENT.txt") -Value $note -Encoding UTF8
}

function Remove-StaleReleaseFolders {
    param([string]$OutRoot, [string]$KeepLabel)

    if (-not (Test-Path $OutRoot)) { return }
    Get-ChildItem -Path $OutRoot -Directory -Filter "mensa_client_*" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -ne $KeepLabel) {
            Write-Host "Removing stale release folder: $($_.Name)" -ForegroundColor DarkGray
            try {
                Remove-Item -Recurse -Force $_.FullName -ErrorAction Stop
            } catch {
                Write-Host "  (skipped - folder in use: $($_.Name))" -ForegroundColor Yellow
            }
        }
    }
}

# --- Main ---
Write-Host "Mensa distribution packager" -ForegroundColor Green
Write-Host "Version: $Version"
Write-Host "Output:  $PackageDir"

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
Remove-StaleReleaseFolders -OutRoot (Join-Path $Root $OutputRoot) -KeepLabel $PackageName

Test-DockerReady

if ($IncludeImages) {
    Ensure-BuiltImages
}

Copy-PackageFiles -Dest $PackageDir
New-OfflineCompose -Dest $PackageDir
Write-InstallGuide -Dest $PackageDir
New-ClientReadme -Dest $PackageDir

if ($IncludeImages) {
    Export-DockerImages -DestDir (Join-Path $PackageDir "images")
}

Write-SendToClientNote -OutRoot (Join-Path $Root $OutputRoot) -Label $PackageName

if ($CreateZip) {
    $zipPath = Join-Path $OutputRoot "$PackageName.zip"
    New-ZipArchive -SourceDir $PackageDir -ZipPath $zipPath -PackageLabel $PackageName
    Write-Host "`nArchives (.tar.gz) created under $OutputRoot" -ForegroundColor Green
}

Write-Host "`nPackage ready: $PackageDir" -ForegroundColor Green
Write-Host "See release\SEND_TO_CLIENT.txt for delivery instructions."