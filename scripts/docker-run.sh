#!/usr/bin/env bash
# Run the Scientific Home Cluster Docker Compose stack in a chosen mode.
#
# Usage:
#   ./scripts/docker-run.sh dev [--rebuild] [--syncthing-root PATH]
#   ./scripts/docker-run.sh prod [--rebuild] [--syncthing-root PATH]
#   ./scripts/docker-run.sh localhost [--rebuild] [--syncthing-root PATH]
#
# dev        - default single-container setup (generated token, localhost URL).
# Note: scripts never start the db-seed service and rely on SEED_DEMO_DATA=false
# (the application default), so no demo data is seeded.
# prod       - production setup; requires VITE_API_URL (and ideally SHARED_TOKEN)
#              in .env or the environment. Builds the image by default.
# localhost  - single-host setup using the localhost defaults (no rebuild).
# --rebuild       - force rebuild of the docker image before starting.
# --syncthing-root PATH - override the Syncthing root directory (default: ./syncthing)

set -euo pipefail

MODE="${1:-dev}"
REBUILD=false
SYNCTHING_ROOT_OVERRIDE=""

# Parse arguments
shift 2>/dev/null || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --rebuild)
            REBUILD=true
            shift
            ;;
        --syncthing-root)
            SYNCTHING_ROOT_OVERRIDE="${2:-}"
            if [[ -z "$SYNCTHING_ROOT_OVERRIDE" ]]; then
                echo "Error: --syncthing-root requires a path argument" >&2
                exit 1
            fi
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Determine Syncthing root: override > env var > default (./syncthing)
SYNCTHING_ROOT="${SYNCTHING_ROOT_OVERRIDE:-${SYNCTHING_ROOT:-./syncthing}}"

# Resolve to absolute path (for Docker bind mount)
if [[ "$SYNCTHING_ROOT" != /* ]]; then
    SYNCTHING_ROOT_ABS="$ROOT_DIR/$SYNCTHING_ROOT"
else
    SYNCTHING_ROOT_ABS="$SYNCTHING_ROOT"
fi

# Ensure Syncthing directory structure exists (jobs/ and nodes/ subdirs)
mkdir -p "$SYNCTHING_ROOT_ABS/jobs" "$SYNCTHING_ROOT_ABS/nodes"

# Export for docker-compose (overrides default ./syncthing)
export SYNCTHING_ROOT="$SYNCTHING_ROOT_ABS"

# Ensure no demo data is seeded: override SEED_DEMO_DATA to false for the backend.
OVERRIDE="$(mktemp)"
trap 'rm -f "$OVERRIDE"' EXIT
cat > "$OVERRIDE" <<'EOF'
services:
  backend:
    environment:
      SEED_DEMO_DATA: "false"
EOF

# Build compose command with optional --build
COMPOSE_CMD="docker compose -f docker-compose.yaml -f \"$OVERRIDE\" up -d"
if [[ "$REBUILD" == "true" ]] || [[ "$MODE" == "prod" ]]; then
    COMPOSE_CMD="$COMPOSE_CMD --build"
fi

case "$MODE" in
  dev)
    echo "Starting development stack (no seed)..."
    eval "$COMPOSE_CMD backend"
    ;;
  prod)
    if [ -z "${VITE_API_URL:-}" ] && ! grep -q '^VITE_API_URL=' .env 2>/dev/null; then
      echo "VITE_API_URL is required for prod mode (set it in .env or the environment)." >&2
      exit 1
    fi
    echo "Starting production stack (build enabled, no seed)..."
    eval "$COMPOSE_CMD backend"
    ;;
  localhost)
    echo "Starting localhost stack (no seed)..."
    eval "$COMPOSE_CMD backend"
    ;;
  *)
    echo "Unknown mode: $MODE (use dev, prod, or localhost)" >&2
    exit 1
    ;;
esac

docker compose ps