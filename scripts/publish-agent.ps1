# Publish the built agent binary and assets to the Syncthing folder.
#
# Usage:
#   .\scripts\publish-agent.ps1
#   .\scripts\publish-agent.ps1 -Target linux
#   .\scripts\publish-agent.ps1 -Target windows
#
# Requires the agent binary to be built first (run .\scripts\build-agent.ps1 first).

param(
    [ValidateSet("", "linux", "windows")]
    [string]$Target = ""
)

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

$targetArg = if ($Target) { "--target $Target" } else { "" }

Write-Host "[publish-agent] Publishing agent to Syncthing folder..."
python scripts/publish_agent.py $targetArg
if ($LASTEXITCODE -ne 0) {
    Write-Error "[publish-agent] Publish failed."
    exit 1
}

Write-Host "[publish-agent] Done. Binary and assets published to syncthing\ folder."