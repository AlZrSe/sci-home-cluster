# Build the standalone worker-agent executable (run-agent / run-agent.exe).
#
# Usage:
#   .\scripts\build-agent.ps1
#   .\scripts\build-agent.ps1 -Target linux
#   .\scripts\build-agent.ps1 -Target windows

param(
    [ValidateSet("", "linux", "windows")]
    [string]$Target = ""
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

Write-Host "[build-agent] Building agent $targetArg..."
python scripts/build_agent.py $targetArg
if ($LASTEXITCODE -ne 0) {
    Write-Error "[build-agent] Build failed."
    exit 1
}

Write-Host "[build-agent] Build complete. Run .\scripts\publish-agent.ps1 to publish to Syncthing folder."