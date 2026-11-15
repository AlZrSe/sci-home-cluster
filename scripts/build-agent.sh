#!/usr/bin/env bash
# Build the standalone worker-agent executable (run-agent / run-agent.exe) and
# publish it into the Syncthing folder so it replicates to every node.
#
# Usage:
#   ./scripts/build-agent.sh
#   ./scripts/build-agent.sh linux
#   ./scripts/build-agent.sh windows --no-publish

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[build-agent] Installing build dependencies..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev,build]"

TARGET=""
NOPUBLISH=""
for arg in "$@"; do
    case "$arg" in
        linux|windows) TARGET="--target $arg" ;;
        --no-publish) NOPUBLISH="--no-publish" ;;
    esac
done

echo "[build-agent] Building agent..."
python scripts/build_agent.py $TARGET $NOPUBLISH

echo "[build-agent] Done. Binary published to syncthing/ (unless --no-publish)."
