# Build the standalone worker-agent executable (run-agent / run-agent.exe) and
# publish it into the Syncthing folder so it replicates to every node.
#
# Usage:
#   .\scripts\build-agent.ps1
#   .\scripts\build-agent.ps1 -Target linux
#   .\scripts\build-agent.ps1 -Target windows -NoPublish

param(
    [ValidateSet("", "linux", "windows")]
    [string]$Target = "",
    [switch]$NoPublish
)

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

Write-Host "[build-agent] Installing build dependencies..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev,build]"
if ($LASTEXITCODE -ne 0) {
    Write-Error "[build-agent] Failed to install dependencies."
    exit 1
}

$targetArg = if ($Target) { "--target $Target" } else { "" }
$publishArg = if ($NoPublish) { "--no-publish" } else { "" }

Write-Host "[build-agent] Building agent $targetArg..."
python scripts/build_agent.py $targetArg $publishArg
if ($LASTEXITCODE -ne 0) {
    Write-Error "[build-agent] Build failed."
    exit 1
}

Write-Host "[build-agent] Done. Binary published to syncthing\ (unless -NoPublish)."
