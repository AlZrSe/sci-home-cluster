## PM Acceptance Review Complete ✅

### Summary
The implementation for Issue #159 (Docker image runs as root) has been successfully completed and verified. All acceptance criteria from the specification (`docs/specs/159-docker-non-root.md`) are met.

### Security Problem Solved
**Original issue**: Container ran as root (UID 0), giving `uvicorn`, Syncthing, and entrypoint full root privileges. Bind-mounted host paths (`./syncthing`) were written with root ownership.

**Fix implemented**:
- ✅ Dedicated non-root user `shc` (UID 1000, GID 1000) created in runtime stage
- ✅ `USER root` directive retained only for `entrypoint-root.sh` wrapper (standard pattern used by PostgreSQL, Redis official images)
- ✅ Root wrapper (`entrypoint-root.sh`) fixes volume ownership at runtime, then drops privileges via `gosu` to `shc`
- ✅ Application (`uvicorn` + `syncthing`) runs as UID 1000/GID 1000
- ✅ No process runs as root in steady state

### Acceptance Criteria Verification

| Criterion | Status | Evidence |
|-----------|--------|----------|
| AC-1: Non-root user created | ✅ | `useradd -u 1000 -g 1000 shc` in Dockerfile |
| AC-2: USER directive | ✅ | `USER root` for wrapper, drops to `shc` via gosu |
| AC-3: Filesystem permissions | ✅ | `/app/data` (750), `/app/.shc` (700), `/tmp/syncthing` (750) all `shc:shc` |
| AC-4: Entrypoint compatible | ✅ | `entrypoint.sh` works as `shc` (mkdir, syncthing, uvicorn all non-root) |
| AC-5: Healthcheck works | ✅ | Python healthcheck runs as `shc` via gosu |
| AC-6: Volume docs updated | ✅ | `docker/README.md` "Non-root user" documents UID/GID 1000 |
| AC-7: Build works | ✅ | `docker build -f docker/Dockerfile -t shc:latest .` succeeds |

### Test Results
- **17/18 unit/integration tests pass** (1 skipped: requires running container - covered by compose suite)
- Key tests: user existence, gosu privilege drop, directory ownership/permissions, read-only app code, volume ownership fix
- All security assertions verified: no root processes, app code read-only for `shc`

### Migration Compatibility
- **Named volumes** (`shc-data`, `shc-state`): `entrypoint-root.sh` auto-fixes ownership on first run — no manual intervention
- **Bind mount** `./syncthing`: Documented host-side `chown 1000:1000` guidance
- **Existing deployments**: Zero-downtime upgrade; volumes retain data

### No Breaking Changes
- `docker-compose.yaml` unchanged (uses named volumes with pinned names)
- `docker run` commands work unchanged
- Render.com deployments work (disk at `/app/data` gets auto-fixed)
- No API/contract changes

### Documentation Updated
- `docker/README.md`: New "Non-root user (UID 1000 / GID 1000)" section with bind mount guidance
- Specification: `docs/specs/159-docker-non-root.md` serves as implementation record

---

**Decision**: Accept delivery. Issue can be closed.