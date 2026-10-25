# Specification: Docker Image Non-Root User (Issue #159)

## Overview

The current Docker image runs as `root` (UID 0) because no `USER` directive is specified in the Dockerfile. This means:
- `uvicorn` (API server) runs as root
- `syncthing` daemon runs as root
- Entrypoint script runs as root
- All bind-mounted host paths (especially `./syncthing`) are written with root ownership
- A vulnerability in the application or agent-launched job would have root privileges inside the container

This specification defines the work to run the container as a dedicated non-root user with proper filesystem permissions.

---

## User Stories

### US-1: Security Hardening
**As a** cluster operator  
**I want** the container to run as a non-root user  
**So that** a compromise of the application or Syncthing does not grant root privileges inside the container

### US-2: Host Filesystem Hygiene
**As a** cluster operator  
**I want** files created in bind-mounted volumes (`./syncthing`, `shc-data`, `shc-state`) to be owned by a consistent non-root UID/GID  
**So that** host filesystem permissions remain manageable and predictable

### US-3: Compliance & Best Practices
**As a** security-conscious deployer  
**I want** the image to follow Docker security best practices (non-root USER, read-only root filesystem where feasible)  
**So that** the image passes container security scans and organizational policy checks

### US-4: Operational Simplicity
**As a** cluster operator  
**I want** clear documentation of the UID/GID used by the container  
**So that** I can configure host-side bind mounts with matching ownership or use `--user` overrides correctly

---

## Acceptance Criteria

### AC-1: Non-Root User Creation
- [ ] A dedicated user `shc` (UID 1000) and group `shc` (GID 1000) are created in the runtime stage
- [ ] The user has a home directory at `/home/shc` (or `/app` if preferred)
- [ ] The user's shell is `/bin/bash` (for entrypoint compatibility)

### AC-2: Runtime USER Directive
- [ ] The Dockerfile includes `USER shc` (or `USER 1000:1000`) after all root-required operations
- [ ] The container runs as UID 1000, GID 1000 by default
- [ ] `docker inspect` shows `"User": "1000:1000"` (or `"shc"`)

### AC-3: Filesystem Permissions
- [ ] `/app/data` (SQLite database, Syncthing home) is owned by `shc:shc` with `0750` permissions
- [ ] `/app/.shc` (JWT signing key, shared_token) is owned by `shc:shc` with `0700` permissions
- [ ] `/tmp/syncthing` (SYNCTHING_ROOT bind mount target) is owned by `shc:shc` with `0750` permissions
- [ ] `/app/frontend` (static assets) is readable by `shc` (world-readable `0644` files, `0755` dirs)
- [ ] `/usr/local/bin/syncthing` and `/usr/local/bin/entrypoint.sh` are executable by `shc`

### AC-4: Entrypoint Compatibility
- [ ] `entrypoint.sh` works correctly when run as `shc` (no root-only operations)
- [ ] `mkdir -p "$SYNCTHING_HOME" "$SYNCTHING_ROOT"` succeeds (directories already exist with correct ownership)
- [ ] `syncthing -generate="$SYNCTHING_HOME"` writes config as `shc`
- [ ] Python XML config tuning script runs as `shc`
- [ ] `syncthing` daemon starts as background process under `shc`
- [ ] `uvicorn` starts as `shc` on port 8000 (non-privileged port)

### AC-5: Healthcheck Compatibility
- [ ] Healthcheck command runs successfully as `shc`
- [ ] Healthcheck uses `python -c ...` which works without root

### AC-6: Volume Mount Documentation
- [ ] Documentation specifies UID 1000 / GID 1000 for bind mounts
- [ ] `docker/README.md` updated with host-side `chown` guidance
- [ ] `docker-compose.yaml` (if exists at repo root) works without modification (volumes use named volumes, not bind mounts for data)

### AC-7: Build Still Works
- [ ] `docker build -f docker/Dockerfile -t shc:latest .` succeeds
- [ ] Frontend build stage unaffected (runs as root in build stage, which is fine)
- [ ] Runtime stage copies frontend assets with correct ownership

---

## Technical Design

### User/Group Definition

```dockerfile
# In runtime stage (python:3.11-slim-bookworm)
RUN groupadd -g 1000 shc \
 && useradd -u 1000 -g 1000 -m -s /bin/bash -d /home/shc shc
```

**Why UID/GID 1000?**
- Standard first non-root user on most Linux distributions
- Matches typical host user UID, reducing permission friction for bind mounts
- Avoids conflicts with system users (typically < 1000)

### Directory Ownership Strategy

| Path | Purpose | Owner | Permissions | Notes |
|------|---------|-------|-------------|-------|
| `/app` | Application root | `root:root` | `0755` | Read-only for `shc` at runtime |
| `/app/backend`, `/app/shared`, `/app/agent`, `/app/cli` | Python packages | `root:root` | `0755` | Installed via `pip install .` |
| `/app/frontend` | Static assets | `root:root` | `0755`/`0644` | World-readable, served by uvicorn |
| `/app/data` | SQLite DB + Syncthing home | `shc:shc` | `0750` | **Volume mount point** |
| `/app/.shc` | JWT signing key | `shc:shc` | `0700` | **Volume mount point**, secret storage |
| `/tmp/syncthing` | Syncthing folder root | `shc:shc` | `0750` | **Bind mount target** (`SYNCTHING_ROOT`) |
| `/home/shc` | User home | `shc:shc` | `0750` | Default home directory |

### Dockerfile Changes (Runtime Stage)

```dockerfile
# ... existing runtime stage setup ...

# Create non-root user
RUN groupadd -g 1000 shc \
 && useradd -u 1000 -g 1000 -m -s /bin/bash -d /home/shc shc

# ... existing COPY, pip install, frontend copy ...

# Create and chown runtime directories BEFORE switching user
RUN mkdir -p /app/data /app/.shc /tmp/syncthing \
 && chown -R shc:shc /app/data /app/.shc /tmp/syncthing \
 && chmod 750 /app/data /tmp/syncthing \
 && chmod 700 /app/.shc

# Volumes declared after chown so mount inherits ownership
VOLUME ["/app/data", "/app/.shc"]

# ... ENV, EXPOSE, HEALTHCHECK ...

# Switch to non-root user
USER shc

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

### Entrypoint Changes

The entrypoint needs minimal changes since directories will pre-exist with correct ownership:

```bash
#!/usr/bin/env bash
set -euo pipefail

SYNCTHING_HOME="${SYNCTHING_HOME:-/app/data/syncthing}"
SYNCTHING_ROOT="${SYNCTHING_ROOT:-/tmp/syncthing}"
PORT="${PORT:-8000}"

# Directories already exist with correct ownership (created in Dockerfile)
# mkdir -p "$SYNCTHING_HOME" "$SYNCTHING_ROOT"  # Can keep for safety

export STNOUPGRADE=1

# First run: generate identity + default config.
if [ ! -f "$SYNCTHING_HOME/config.xml" ]; then
    syncthing -generate="$SYNCTHING_HOME" >/dev/null 2>&1
fi

# ... rest unchanged ...
```

**Note**: The `mkdir -p` can remain for defense-in-depth (handles edge cases where volume mount is empty), but it will be a no-op since directories exist.

### Volume Mount Considerations

#### Named Volumes (`shc-data`, `shc-state`)
- Docker creates them with root ownership initially
- Our `chown` in Dockerfile runs at **build time**, not runtime
- **Problem**: At runtime, when a named volume is mounted, it overlays the image's `/app/data` and `/app/.shc`, hiding the pre-chowned directories
- **Solution**: The `chown` must happen at **runtime** in the entrypoint, OR we accept that named volumes start root-owned and fix on first run

**Recommended approach**: Handle in entrypoint at runtime (idempotent):
```bash
# In entrypoint.sh, before any writes:
# Ensure runtime directories have correct ownership (handles fresh named volumes)
chown -R shc:shc /app/data /app/.shc 2>/dev/null || true
# But we're already shc! Can't chown as non-root.
```

**Better approach**: Use an init container or fix at build time with a known UID/GID that matches host expectations. Since we control the UID/GID (1000:1000), the cleanest solution:

1. **Build-time**: Create directories and chown in Dockerfile (for the image's filesystem)
2. **Runtime**: When named volume mounts, it starts with root ownership. The entrypoint runs as UID 1000 and **cannot** chown.
3. **Workaround**: Document that on first run with fresh named volumes, admin must `docker compose exec -u root backend chown -R 1000:1000 /app/data /app/.shc`, OR use a root-entrypoint wrapper that drops privileges.

**Simpler pragmatic approach**: Since the entrypoint runs as UID 1000, and the volume starts root-owned, the first write by `shc` will fail with permission denied.

**Solution**: Use a **root entrypoint wrapper** that fixes permissions then drops to `shc`:

```dockerfile
# Dockerfile
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/entrypoint-root.sh /usr/local/bin/entrypoint-root.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/entrypoint-root.sh

USER root
ENTRYPOINT ["/usr/local/bin/entrypoint-root.sh"]
```

```bash
# entrypoint-root.sh
#!/usr/bin/env bash
set -euo pipefail

# Fix ownership of volume mount points (idempotent)
chown -R 1000:1000 /app/data /app/.shc /tmp/syncthing 2>/dev/null || true

# Drop to shc user and exec the real entrypoint
exec gosu shc /usr/local/bin/entrypoint.sh "$@"
```

**But `gosu` is not installed**. Alternative: `su-exec` (from `su-exec` package) or `runuser`/`su`.

**Minimal approach**: Install `su-exec` (tiny, ~30KB):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends su-exec && rm -rf /var/lib/apt/lists/*
```

Then:
```bash
# entrypoint-root.sh
exec su-exec shc /usr/local/bin/entrypoint.sh "$@"
```

This is the cleanest pattern used by official images (PostgreSQL, Redis, etc.).

### Bind Mount: `./syncthing` → `/tmp/syncthing`

- Host path `./syncthing` is created by `docker/init_env.py`
- Container's `/tmp/syncthing` is the bind mount target
- With `USER 1000:1000`, files created in `/tmp/syncthing` will be owned by 1000:1000 on host
- **Documentation**: Host should ensure `./syncthing` is owned by UID 1000, or accept that container writes will be UID 1000

---

## Test Scenarios

### Unit Tests (Backend - pytest)

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-UT-01 | Dockerfile builds successfully | `docker build` exits 0 |
| TC-UT-02 | Image has USER directive | `docker inspect shc:latest --format '{{.Config.User}}'` returns `1000:1000` or `shc` |
| TC-UT-03 | Non-root user exists in image | `docker run --rm shc:latest id` returns `uid=1000(shc) gid=1000(shc)` |
| TC-UT-04 | Entrypoint script is executable by shc | `docker run --rm --user 1000:1000 shc:latest test -x /usr/local/bin/entrypoint.sh` |

### Integration Tests (Compose Suite - `backend/tests/compose/`)

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-IT-01 | Container starts and becomes healthy | `docker compose up -d` → `docker compose ps` shows `(healthy)` |
| TC-IT-02 | API responds on port 8000 | `curl http://localhost:8000/api/v1/health` returns 200 |
| TC-IT-03 | Frontend served correctly | `curl http://localhost:8000/` returns HTML with `_shell.html` content |
| TC-IT-04 | Files in `shc-data` volume owned by 1000:1000 | `docker compose exec -u root backend stat /app/data` shows UID 1000 |
| TC-IT-05 | Files in `shc-state` volume owned by 1000:1000 | `docker compose exec -u root backend stat /app/.shc` shows UID 1000 |
| TC-IT-06 | Syncthing creates folders in bind mount as 1000:1000 | `ls -la ./syncthing` shows UID 1000 ownership |
| TC-IT-07 | JWT signing key created and readable by shc | `docker compose exec backend cat /app/.shc/shared_token` works |
| TC-IT-08 | Database file created as 1000:1000 | `docker compose exec -u root backend stat /app/data/scientific_home_cluster.db` shows UID 1000 |
| TC-IT-09 | Container restart preserves permissions | `docker compose restart backend` → health check passes, permissions intact |
| TC-IT-10 | `down -v` and `up -d` recreates with correct permissions | Fresh volumes get correct ownership via entrypoint-root.sh |

### Security Tests

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-SEC-01 | No process runs as root | `docker compose exec backend ps aux` shows no UID 0 processes |
| TC-SEC-02 | Cannot write to `/app` (read-only app code) | `docker compose exec backend touch /app/test` fails with EACCES |
| TC-SEC-03 | Cannot write to `/usr/local/bin` | `docker compose exec backend touch /usr/local/bin/test` fails |

### Migration/Compatibility Tests

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-MIG-01 | Existing `shc-data` volume (root-owned) works after upgrade | Entryoint-root.sh fixes ownership, app starts |
| TC-MIG-02 | Existing `shc-state` volume (root-owned) works after upgrade | JWT key readable, tokens still valid |
| TC-MIG-03 | Host bind mount `./syncthing` with existing root-owned files | Container can read/write (UID 1000 matches or perms allow) |

---

## API Changes

**None.** This is a pure infrastructure/runtime change. No API contracts, schemas, or endpoints are modified.

---

## Migration Notes

### For Existing Deployments

1. **Named volumes (`shc-data`, `shc-state`)**:
   - On first run with new image, the `entrypoint-root.sh` wrapper will `chown -R 1000:1000` the volume mount points
   - This is automatic and idempotent
   - No manual intervention needed

2. **Bind mount `./syncthing`**:
   - If host directory already exists with root-owned files, container (UID 1000) may not read/write
   - **Fix**: `sudo chown -R 1000:1000 ./syncthing` on host before upgrading
   - Or run container once with `--user root` to fix, then restart normally

3. **docker-compose.yaml**: No changes required. Volumes use named volumes with pinned names.

4. **Render.com / Cloud Deployments**:
   - Disk mounted at `/app/data` — entrypoint-root.sh will fix ownership on first start
   - `SHC_STATE_DIR=/app/data/.shc` — same volume, same fix

### For Developers

- Local `docker compose up -d` works unchanged
- `make test-compose` works unchanged (uses throwaway `shc-e2e-*` volumes)
- No changes to `.env` or environment variables

---

## Rollback Plan

If issues arise:
1. Revert Dockerfile to previous version (no USER, no entrypoint-root.sh)
2. Rebuild image: `docker build -f docker/Dockerfile -t shc:latest .`
3. Restart: `docker compose up -d`
4. Volumes retain data; ownership reverts to root (may need `chown` if non-root processes need access)

---

## References

- [Docker Security Best Practices: Non-Root User](https://docs.docker.com/develop/security-best-practices/#run-as-a-non-root-user)
- [Official Images: su-exec pattern](https://github.com/docker-library/postgres/blob/master/Dockerfile.debian.template#L112)
- Issue #154 (Syncthing static build) — entrypoint already manages Syncthing
- Issue #104 (Silent bind mount failure) — related to SYNCTHING_ROOT permissions
- Issue #130 (DB_POOL=pooled) — unrelated but noted in docker/README.md
- `docker/README.md` — "Stopping, and what `down -v` costs" section mentions root ownership issue

---

## Definition of Done

- [ ] Specification reviewed and approved by PM
- [ ] Dockerfile updated with non-root user, su-exec, entrypoint-root.sh
- [ ] entrypoint.sh verified compatible (no root-only ops)
- [ ] docker/README.md updated with UID/GID documentation
- [ ] All unit tests pass (`pytest backend/tests/unit`)
- [ ] All compose tests pass (`make test-compose` with `SHC_COMPOSE_TESTS=1 SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`)
- [ ] Security scans pass (no root processes, read-only app code)
- [ ] PM accepts delivery

---

*Generated for Issue #159 — Docker image runs as root; no non-root USER directive*