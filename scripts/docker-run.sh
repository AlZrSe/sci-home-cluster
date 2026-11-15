#!/usr/bin/env bash
# Run the Scientific Home Cluster Docker Compose stack in a chosen mode.
#
# Usage:
#   ./scripts/docker-run.sh dev
#   ./scripts/docker-run.sh prod
#   ./scripts/docker-run.sh localhost
#
# dev        - default single-container setup (generated token, localhost URL).
# Note: scripts never start the db-seed service and rely on SEED_DEMO_DATA=false
# (the application default), so no demo data is seeded.
# prod       - production setup; requires VITE_API_URL (and ideally SHARED_TOKEN)
#              in .env or the environment. Builds the image.
# localhost  - single-host setup using the localhost defaults (no rebuild).

set -euo pipefail

MODE="${1:-dev}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Ensure no demo data is seeded: override SEED_DEMO_DATA to false for the backend.
OVERRIDE="$(mktemp)"
trap 'rm -f "$OVERRIDE"' EXIT
cat > "$OVERRIDE" <<'EOF'
services:
  backend:
    environment:
      SEED_DEMO_DATA: "false"
EOF

case "$MODE" in
  dev)
    echo "Starting development stack (no seed)..."
    docker compose -f docker-compose.yaml -f "$OVERRIDE" up -d backend
    ;;
  prod)
    if [ -z "${VITE_API_URL:-}" ] && ! grep -q '^VITE_API_URL=' .env 2>/dev/null; then
      echo "VITE_API_URL is required for prod mode (set it in .env or the environment)." >&2
      exit 1
    fi
    echo "Starting production stack (build enabled, no seed)..."
    docker compose -f docker-compose.yaml -f "$OVERRIDE" up -d --build backend
    ;;
  localhost)
    echo "Starting localhost stack (no seed)..."
    docker compose -f docker-compose.yaml -f "$OVERRIDE" up -d backend
    ;;
  *)
    echo "Unknown mode: $MODE (use dev, prod, or localhost)" >&2
    exit 1
    ;;
esac

docker compose ps