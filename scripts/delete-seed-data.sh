#!/usr/bin/env bash
# Delete demo seed data from the SHC database.
# Usage:
#   ./scripts/delete-seed-data.sh [--db PATH] [--docker]
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
python scripts/delete_seed_data.py "$@"