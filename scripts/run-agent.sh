#!/usr/bin/env bash
# Run the worker agent from the Syncthing folder.
#
# Usage:
#   ./scripts/run-agent.sh
#
# The Syncthing folder must contain:
#   - run-agent (the agent binary, published via build-agent.sh)
#   - .env (node configuration with NODE_ID, SYNCTHING_ROOT, etc.)
#
# This script is meant to be run from the Syncthing folder on each node.
# All parameters are configured inside this script.

set -euo pipefail

# ============================================================
# CONFIGURATION - EDIT THESE VALUES FOR YOUR NODE
# ============================================================
NODE_ID="node-01"
SYNCTHING_ROOT="$(pwd)"
# ============================================================

set -euo pipefail

echo "[run-agent] Starting agent from Syncthing folder: $SYNCTHING_ROOT"

# Resolve Syncthing root to absolute path
SYNCTHING_ROOT="$(cd "$SYNCTHING_ROOT" && pwd)"

# Ensure we're in the Syncthing folder (where the binary lives)
if [[ ! -f "$SYNCTHING_ROOT/run-agent" ]]; then
    echo "[run-agent] Error: run-agent not found at '$SYNCTHING_ROOT/run-agent'." >&2
    echo "[run-agent] Run ./scripts/build-agent.sh first to build and publish the agent." >&2
    exit 1
fi

if [[ ! -f "$SYNCTHING_ROOT/.env" ]]; then
    echo "[run-agent] Warning: .env not found. Agent will prompt on first run." >&2
fi

echo "[run-agent] Starting agent: node=$NODE_ID, syncthing_root=$SYNCTHING_ROOT"
cd "$SYNCTHING_ROOT"
exec ./run-agent --node-id "$NODE_ID" --syncthing-root "$SYNCTHING_ROOT"