#!/usr/bin/env bash
# Root entrypoint wrapper for non-root container execution.
# Fixes ownership of volume mount points, then drops privileges to the shc user.
# Pattern used by official images (PostgreSQL, Redis, etc.).

set -euo pipefail

# Fix ownership of volume mount points (idempotent, runs as root)
# These directories may be fresh named volumes with root ownership,
# or bind mounts with arbitrary host ownership.
chown -R 1000:1000 /app/data /app/.shc /tmp/syncthing 2>/dev/null || true

# Ensure correct permissions
chmod 750 /app/data /tmp/syncthing 2>/dev/null || true
chmod 700 /app/.shc 2>/dev/null || true

# Drop to shc user and exec the real entrypoint
# gosu is installed in the Dockerfile (used by official images)
exec gosu shc /usr/local/bin/entrypoint.sh "$@"