# Run the Scientific Home Cluster Docker Compose stack in a chosen mode.
#
# Usage:
#   .\scripts\docker-run.ps1 -Mode dev [-Rebuild] [-SyncthingRoot "path"]
#   .\scripts\docker-run.ps1 -Mode prod [-Rebuild] [-SyncthingRoot "path"]
#   .\scripts\docker-run.ps1 -Mode localhost [-Rebuild] [-SyncthingRoot "path"]
#
# Note: scripts never start the db-seed service and rely on SEED_DEMO_DATA=false
# (the application default), so no demo data is seeded.
# --Rebuild       - force rebuild of the docker image before starting (default for prod).
# --SyncthingRoot - override the Syncthing root directory (default: ./syncthing).
param(
    [ValidateSet("dev", "prod", "localhost")]
    [string]$Mode = "dev",
    [switch]$Rebuild,
    [string]$SyncthingRoot
)

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

# Determine Syncthing root: parameter > env var > default (./syncthing)
$syncthingRoot = $SyncthingRoot
if (-not $syncthingRoot) {
    $syncthingRoot = $env:SYNCTHING_ROOT
}
if (-not $syncthingRoot) {
    $syncthingRoot = ".\syncthing"
}

# Resolve to absolute path (for Docker bind mount)
# If relative path, make it relative to repo root
if (-not [System.IO.Path]::IsPathRooted($syncthingRoot)) {
    $syncthingRoot = Join-Path $RootDir $syncthingRoot
}

# Convert to absolute path (resolve . and ..)
$syncthingRoot = [System.IO.Path]::GetFullPath($syncthingRoot)

# Ensure Syncthing directory structure exists (jobs/ and nodes/ subdirs)
$jobsDir = Join-Path $syncthingRoot "jobs"
$nodesDir = Join-Path $syncthingRoot "nodes"
if (-not (Test-Path $jobsDir)) { New-Item -ItemType Directory -Path $jobsDir -Force | Out-Null }
if (-not (Test-Path $nodesDir)) { New-Item -ItemType Directory -Path $nodesDir -Force | Out-Null }

# Export for docker-compose (overrides default ./syncthing)
$env:SYNCTHING_ROOT = $syncthingRoot

# Ensure no demo data is seeded: override SEED_DEMO_DATA to false for the backend.
$override = New-TemporaryFile
Set-Content -Path $override -Value @"
services:
  backend:
    environment:
      SEED_DEMO_DATA: "false"
"@ -Encoding utf8

try {
    # Determine if we should build
    $shouldBuild = $Rebuild.IsPresent -or ($Mode -eq "prod")

    $composeCmd = "docker compose -f docker-compose.yaml -f $override up -d"
    if ($shouldBuild) {
        $composeCmd += " --build"
    }
    $composeCmd += " backend"

    switch ($Mode) {
    "dev" {
        Write-Host "Starting development stack (no seed)..."
        Invoke-Expression $composeCmd
    }
    "prod" {
        $hasEnvVar = -not [string]::IsNullOrWhiteSpace($env:VITE_API_URL)
        $hasEnvFile = $false
        if (Test-Path ".env") {
            $hasEnvFile = Select-String -Path ".env" -Pattern '^VITE_API_URL=' -Quiet
        }
        if (-not $hasEnvVar -and -not $hasEnvFile) {
            Write-Error "VITE_API_URL is required for prod mode (set it in .env or the environment)."
            exit 1
        }
        Write-Host "Starting production stack (build enabled, no seed)..."
        Invoke-Expression $composeCmd
    }
    "localhost" {
        Write-Host "Starting localhost stack (no seed)..."
        Invoke-Expression $composeCmd
    }
}
} finally {
    Remove-Item -Path $override -ErrorAction SilentlyContinue
}

docker compose ps