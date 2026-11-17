#!/usr/bin/env bash
# Publish the built agent binary and assets to the Syncthing folder.
#
# Usage:
#   ./scripts/publish-agent.sh
#   ./scripts/publish-agent.sh linux
#   ./scripts/publish-agent.sh windows
#
# Requires the agent binary to be built first (run ./scripts/build-agent.sh first).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TARGET=""
for arg in "$@"; do
    case "$arg" in
        linux|windows) TARGET="--target $arg" ;;
    esac
done

echo "[publish-agent] Publishing agent to Syncthing folder..."
python scripts/publish_agent.py $TARGET

echo "[publish-agent] Done. Binary and assets published to syncthing/ folder."