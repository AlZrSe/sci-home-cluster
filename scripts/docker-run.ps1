# Run the Scientific Home Cluster Docker Compose stack in a chosen mode.
#
# Usage:
#   .\scripts\docker-run.ps1 -Mode dev [-Rebuild]
#   .\scripts\docker-run.ps1 -Mode prod [-Rebuild]
#   .\scripts\docker-run.ps1 -Mode localhost [-Rebuild]
#
# Note: scripts never start the db-seed service and rely on SEED_DEMO_DATA=false
# (the application default), so no demo data is seeded.
# --Rebuild  - force rebuild of the docker image before starting (default for prod).
param(
    [ValidateSet("dev", "prod", "localhost")]
    [string]$Mode = "dev",
    [switch]$Rebuild
)

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

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