#!/usr/bin/env bash
# Build the standalone worker-agent executable (run-agent / run-agent.exe).
#
# Usage:
#   ./scripts/build-agent.sh
#   ./scripts/build-agent.sh linux
#   ./scripts/build-agent.sh windows

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[build-agent] Installing build dependencies..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev,build]"

TARGET=""
for arg in "$@"; do
    case "$arg" in
        linux|windows) TARGET="--target $arg" ;;
    esac
done

echo "[build-agent] Building agent..."
python scripts/build_agent.py $TARGET

echo "[build-agent] Build complete. Run ./scripts/publish-agent.sh to publish to Syncthing folder."