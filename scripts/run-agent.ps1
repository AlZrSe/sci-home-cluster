# Run the worker agent from the Syncthing folder.
#
# Usage:
#   .\scripts\run-agent.ps1
#
# The Syncthing folder must contain:
#   - run-agent.exe (the agent binary, published via build-agent.ps1)
#   - .env (node configuration with NODE_ID, SYNCTHING_ROOT, etc.)
#
# This script is meant to be run from the Syncthing folder on each node.
# All parameters are configured inside this script.

# ============================================================
# CONFIGURATION - EDIT THESE VALUES FOR YOUR NODE
# ============================================================
$NodeId = "node-01"
$SyncthingRoot = (Get-Location).Path
# ============================================================

# Resolve Syncthing root to absolute path
$SyncthingRoot = Resolve-Path $SyncthingRoot

# Ensure we're in the Syncthing folder (where the binary lives)
if (-not (Test-Path (Join-Path $SyncthingRoot "run-agent.exe"))) {
    Write-Error "[run-agent] Error: run-agent.exe not found at '$SyncthingRoot\run-agent.exe'."
    Write-Error "[run-agent] Run .\scripts\build-agent.ps1 first to build and publish the agent."
    exit 1
}

if (-not (Test-Path (Join-Path $SyncthingRoot ".env"))) {
    Write-Warning "[run-agent] Warning: .env not found. Agent will prompt on first run."
}

Write-Host "[run-agent] Starting agent: node=$NodeId, syncthing_root=$SyncthingRoot"
Set-Location $SyncthingRoot
& .\run-agent.exe --node-id $NodeId --syncthing-root $SyncthingRoot