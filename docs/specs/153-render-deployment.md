# Spec: Render.com Deployment Support

**Issue**: #153 — "Add deployment support for Render.com"
**Labels**: `enhancement`, `phase-3`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-23
**Baseline**: `034eb328` (`main`), branch `main`
**Measured on**: Windows 11, Docker Desktop (for compose gate), pinned toolchain per `AGENTS.md`

**This file is the authoritative input for the SWE cycle.** It is self-sufficient;
§3 carries the decisions, §4 carries the code shapes. §5 is the acceptance
checklist. No code is implemented by this spec.

---

## 0. Baseline, as re-measured by PM

| Gate | On `034eb328` |
|---|---|
| `ruff check .` | expected clean (not re-run here; unchanged tree) |
| `ruff format --check .` | expected clean |
| `mypy .` | expected clean |
| `pytest --cov=backend` | expected green; compose suite skipped unless `SHC_COMPOSE_TESTS=1` |
| Compose L1 static (`SHC_COMPOSE_TESTS=1 ... -k config`) | `docker compose config` resolves; pinned volumes `shc-data`/`shc-state` |

The changes in this spec must not move any of those needles: backend gates stay
green, and `backend/tests/compose/` (L1–L5) keeps passing on the checked-in
`docker-compose.yaml`. The new `render.yaml` is additive and is not part of the
compose suite's file set, so it cannot regress those tests by construction.

---

## 1. Verified Problem Statement

### 1.1 The image hard-codes its port in two places

`docker/Dockerfile`:

- line 132 `EXPOSE 8000`
- line 137 `HEALTHCHECK … urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)`
- line 139 `CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]`

Render injects `PORT` (default 10000 for web services) and requires the process
to listen on exactly that port. A container that always binds 8000 fails
Render's health probe and is marked unhealthy / killed. `docker-compose.yaml`
also publishes the constant `8000:8000` (line 120) and documents that the port
is deliberately not derived from `VITE_API_URL` (lines 112–118); that contract
must survive.

### 1.2 State lives on an ephemeral filesystem unless a disk is attached

The image declares `VOLUME ["/app/data", "/app/.shc"]` (Dockerfile:122) and
bakes `SHC_DATA_DIR=/app/data` (Dockerfile:129). `backend/core/config.py`
resolves the SQLite file to `SHC_DATA_DIR/scientific_home_cluster.db` when
`SHC_DATA_DIR` is set and `DATABASE_URL` is still the default
(`config.py:559-567`), and persists the JWT signing key under
`SHC_STATE_DIR` (default `<repo-root>/.shc`, `config.py:45`). On Render, with no
disk, every deploy wipes both: the cluster state and every issued JWT.

Render allows **one disk per web service**, mounted at a single path. The two
existing volumes therefore cannot both be first-class Render Disks; they must be
co-located under one mount (see §3 D2).

### 1.3 The frontend is a git submodule

`.gitmodules` declares `frontend` → `https://github.com/AlZrSe/rendering-replicate.git`.
`.dockerignore:11` excludes `.git`, so the Dockerfile cannot run
`git submodule update` during the build — it relies on the submodule's files
being present in the build context. `docker/Dockerfile:13-22` documents that a
plain clone (no `--recurse-submodules`) leaves `frontend/` empty and the build
fails on purpose. Render must therefore check out submodules into the build
context, or the frontend stage fails. See §3 D5.

### 1.4 `VITE_API_URL` is a build-time constant with a localhost default

`docker/Dockerfile:66` `ARG VITE_API_URL=http://localhost:8000/api/v1`, and
`frontend/src/lib/settings.ts:20-24` falls back to `http://localhost:8000/api/v1`
when the arg is absent. `frontend/src/services/http.ts` derives the WebSocket URL
with `base().replace(/^http/, "ws")`, and `new WebSocket("/api/v1/...")` throws,
so the base must be absolute (documented in `docker/README.md:52-56` and
`docker-compose.yaml:95-98`). On Render the bundle must point at the service's
public HTTPS URL, or the user must retarget it from the settings page.

### 1.5 Syncthing and worker agents are out of scope for a PaaS

Render cannot host GPU workers or a shared Syncthing folder. A Render deployment
is **API + frontend only**. `backend/main.py` starts `SyncthingService` on
`settings.SYNCTHING_ROOT` (default `/tmp/syncthing`, `config.py:391`); the
directory is created at startup (`syncthing_service.py:190-193`), so the
watcher starts cleanly, reports `syncthing_status: "available"` (because the
directory exists), and no nodes ever register. That is the same silent state
documented in `docker/README.md:162-167` and is acceptable for a frontend/API
demo deployment, but must be documented as a limitation.

### 1.6 Health check path

The app exposes `GET /api/v1/health` (`backend/main.py:317`). It returns
`overall_status: "healthy"` when the store and database are healthy, and a 200
status code (`main.py:355-362`). Render's health check expects a path and a 200;
the path must be configured to `/api/v1/health`. Note that `config_status` is
"degraded" only when `SYNCTHING_ROOT` is empty, which it is not here, and
`overall_status` does not depend on syncthing, so a fresh deploy with an empty
database returns 200.

---

## 2. Root Cause / Constraints (summary)

The image and compose file were built for a self-hosted Docker host: fixed port,
two named volumes, a bind-mounted Syncthing folder, and a localhost-default
frontend. Render is a managed PaaS with a different contract: injected `PORT`,
single disk, ephemeral everything else, HTTPS origin, no GPU workers. Nothing in
the backend needs to change for correctness; the gap is in the container's
port binding, the deployment descriptor, and documentation.

---

## 3. Decisions

### D1 — Port binding honours `PORT` with a 8000 fallback

The container must bind to `${PORT:-8000}`. The fallback preserves every existing
Docker/compose path, which never sets `PORT`. Implementation is a shell-form
`CMD` with `exec` so uvicorn becomes PID 1 and receives `SIGTERM` directly:

```dockerfile
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

The `Dockerfile`'s `HEALTHCHECK` must use the same value, reading `PORT` from
the environment with the same fallback. `EXPOSE 8000` stays as documentation;
Render does not use it.

**Rejected**: baking `ENV PORT=8000` into the image. Render injects `PORT` at
runtime and an image-level value could mask a missing injection during local
testing; the fallback in the command is the single source of truth.

### D2 — One Render Disk co-locates state under `/app/data`

Render permits one disk per service. Mount it at `/app/data` and put the JWT
signing key inside it:

- `SHC_DATA_DIR=/app/data` (already the image default; set explicitly in
  `render.yaml`)
- `SHC_STATE_DIR=/app/data/.shc`

`backend/core/config.py` honours both (`SHC_DATA_DIR` → `DATABASE_URL` redirect
at `config.py:559-567`; `_state_dir()` returns `SHC_STATE_DIR` verbatim at
`config.py:361-378`). `ensure_sqlite_parent_directory()` creates the parent
(`database.py:48-66`), and `_write_state_secret()` creates the state dir
(`config.py:176-177`). No code change is required; only env vars.

The disk survives deploys and restarts, so the SQLite database and the signing
key persist. The Syncthing folder (`/tmp/syncthing`) is intentionally **not**
on the disk — there are no workers to share it with.

### D3 — `DB_POOL=null` on Render

The image bakes `DB_POOL=null` (`Dockerfile:128`) because `pooled` maps to a
bare `QueuePool`, which SQLAlchemy 2.0 rejects on an async engine
(`docker-compose.yaml:194-223`, issue #130). `render.yaml` sets
`DB_POOL=null` explicitly so a Render env-var override cannot reintroduce the
crash. This is not a Render-specific choice; it is the only value that boots.

### D4 — `VITE_API_URL` defaults to the Render service URL

Set it in `render.yaml` to the service's canonical public URL:

```
https://scientific-home-cluster.onrender.com/api/v1
```

Render translates env vars into Docker build args for Docker-based services, so
the `ARG VITE_API_URL` in the Dockerfile receives it at build time. If the
service name (and therefore URL) differs, the operator updates this value and
redeploys. The frontend settings page remains the runtime override path
(`frontend/src/lib/settings.ts:14-24`), so a mismatch is recoverable without a
rebuild. Document both.

### D5 — Submodule checkout is Render's responsibility; verify, don't vendor

The Dockerfile already fails loudly if `frontend/` is empty, which is the
desired behaviour: a deployment that cannot build its UI must not ship a 404ing
image. Render's GitHub integration is expected to initialise submodules into the
build context (the same contract `docker/README.md:7-22` requires of a manual
clone). The spec does **not** vendor the frontend or add a `git submodule`
step to the Dockerfile, because `.git` is excluded from the build context
(`.dockerignore:11`) and a Dockerfile cannot clone. If Render ever stops
initialising submodules, the fallback is an external image pipeline (GitHub
Actions building and pushing to a registry, Render pulling the image) — out of
scope here, documented as a limitation.

### D6 — Health check path `/api/v1/health`

`render.yaml` sets `healthCheckPath: /api/v1/health`. The endpoint returns 200
on a fresh deploy (empty DB, store healthy). Render's own probe supersedes the
image's `HEALTHCHECK`, but the image's probe is kept correct for local Docker
use (D1).

### D7 — Syncthing degrades gracefully; no code change

`SYNCTHING_ROOT=/tmp/syncthing` (created at startup). The dashboard shows no
nodes; jobs cannot be executed. Document as a limitation, not a defect.

---

## 4. Code Shapes

### 4.1 `docker/Dockerfile` (modify)

Current tail (lines 132–139):

```dockerfile
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Replace with:

```dockerfile
EXPOSE 8000

# PORT is injected by Render (and may be set locally); fall back to 8000 so
# the existing docker run / compose paths keep working unchanged.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health'%p, timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

No other Dockerfile changes. `VOLUME ["/app/data", "/app/.shc"]` stays.

### 4.2 `render.yaml` (new, repository root)

```yaml
# render.yaml — Render Blueprint for the Scientific Home Cluster.
# Deploy: Render Dashboard -> New -> Blueprint -> select this repo,
# or `render blueprint validate` (Render CLI) before pushing.
services:
  - type: web
    name: scientific-home-cluster
    runtime: docker
    plan: starter          # disks require a paid instance type
    dockerfilePath: ./docker/Dockerfile
    dockerContext: .
    healthCheckPath: /api/v1/health
    autoDeploy: true
    envVars:
      - key: SERVE_FRONTEND
        value: "true"
      - key: SHC_DATA_DIR
        value: /app/data
      - key: SHC_STATE_DIR
        value: /app/data/.shc
      - key: SYNCTHING_ROOT
        value: /tmp/syncthing
      - key: DB_POOL
        value: "null"
      - key: VITE_CLUSTER_BACKEND
        value: "http"
      - key: VITE_API_URL
        value: "https://scientific-home-cluster.onrender.com/api/v1"
    disk:
      name: shc-data
      mountPath: /app/data
      sizeGB: 1
```

Notes for SWE:
- `PORT` is **not** listed: Render injects it. Listing it would shadow the
  injected value.
- `VITE_API_URL` must be updated if the service name (and thus URL) differs.
- Render translates env vars into Docker build args, so `VITE_CLUSTER_BACKEND`
  and `VITE_API_URL` reach the `ARG`s in the Dockerfile.

### 4.3 Backend / config (no code change required)

No Python changes. The existing mechanisms cover Render:
- `SHC_DATA_DIR` redirect (`config.py:559-567`)
- `SHC_STATE_DIR` verbatim (`config.py:361-378`)
- `ensure_sqlite_parent_directory()` (`database.py:48-66`)
- `_write_state_secret()` creates parent dirs (`config.py:176-177`)

Optional (nice-to-have, not required for AC): a one-line note in
`backend/core/config.py` documenting that `SHC_STATE_DIR` may be nested inside
`SHC_DATA_DIR` on single-disk hosts. Defer unless SWE wants it.

### 4.4 `docker/README.md` (modify — add section)

Add a "Deploying on Render.com" section covering:
- Prerequisites: Render account, GitHub repo connected, submodule initialised
  (verify with a successful local `docker build -f docker/Dockerfile .`).
- Blueprint path: commit `render.yaml`, Render Dashboard → New → Blueprint.
- Manual path: New → Web Service → Docker, Dockerfile path `docker/Dockerfile`,
  health check path `/api/v1/health`, add a Disk mounted at `/app/data`, set
  env vars as in §4.2.
- `PORT` is injected; do not override.
- Disk: one disk holds both DB and JWT key (`SHC_STATE_DIR=/app/data/.shc`).
- `VITE_API_URL`: must be the public HTTPS URL; update and redeploy if the
  service URL differs; runtime override via the settings page.
- Retrieving the generated token:
  `cat /app/data/.shc/shared_token` via Render Shell.
- Limitations: no worker agents, no Syncthing (nodes never register, jobs
  cannot run), ephemeral `/tmp/syncthing`, starter plan required for the disk,
  Render's free tier has no persistent disk.

### 4.5 `docs/deployment-render.md` (new, optional)

A dedicated doc is allowed by the issue ("or a dedicated docs/deployment-render.md").
Either location is acceptable; prefer `docker/README.md` to keep one source of
truth, with a short pointer in `docs/`.

---

## 5. Acceptance Criteria

- [ ] **AC-1** `render.yaml` present at the repository root and valid (Render
      Dashboard import succeeds, or `render blueprint validate` passes).
- [ ] **AC-2** `docker/Dockerfile` binds to `${PORT:-8000}` (CMD) and the
      `HEALTHCHECK` uses the same value; default 8000 preserved for Docker and
      compose.
- [ ] **AC-3** Container starts on Render and serves the SPA + API on one
      origin/port.
- [ ] **AC-4** A Render Disk mounts `/app/data` (single disk; `SHC_STATE_DIR`
      nested inside it) so the DB and JWT key survive deploys.
- [ ] **AC-5** Health check `/api/v1/health` returns 200 on a fresh deploy.
- [ ] **AC-6** Documentation covers submodule initialisation, env vars, disk,
      `VITE_API_URL`, token retrieval, and limitations (no workers, no Syncthing).
- [ ] **AC-7** Backend gates pass: `ruff check . && ruff format --check . &&
      mypy .` clean; `pytest --cov=backend` green.
- [ ] **AC-8** No regression in compose tests: `backend/tests/compose/` (L1–L5)
      still pass; `docker compose config` resolves; pinned volumes
      `shc-data`/`shc-state` unchanged.
- [ ] **AC-9** No new Python dependencies; no changes to `backend/` source
      required (env-var-driven only).

---

## 6. Testing

### 6.1 Local (no Docker daemon required beyond compose gate)

- `docker build -f docker/Dockerfile -t shc:render-test .` with
  `--build-arg VITE_API_URL=https://example.onrender.com/api/v1` succeeds and
  the frontend stage is non-empty (`_shell.html` present) — mirrors L2.
- `PORT=18080 docker run -p 18080:18080 -v shc-data:/app/data -e SHC_STATE_DIR=/app/data/.shc shc:render-test`
  then `curl http://127.0.0.1:18080/api/v1/health` returns 200 and the
  process listens on 18080, not 8000.
- `docker run …` without `PORT` still binds 8000 (fallback).

### 6.2 Compose regression

- `SHC_COMPOSE_TESTS=1 pytest backend/tests/compose -m compose -v` (and the
  destructive scenario with `SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`) remain green.
  The compose file is untouched by this change; the Dockerfile CMD change from
  exec-form uvicorn to `sh -c "exec …"` must not break the L3 health probe or
  L5 lifecycle assertions. Verify the healthcheck still reports `(healthy)`.

### 6.3 Render (manual, out of CI)

- Import the Blueprint; confirm build succeeds (submodule present).
- First deploy: health check green; retrieve token from Render Shell.
- Redeploy: DB row count and JWT validity persist (disk works).
- Settings page can retarget `apiBaseUrl` without a rebuild.

---

## 7. Limitations (documented, not fixed)

- **No worker agents**: Render cannot run GPU workloads; jobs submitted will stay
  pending forever. The cluster is API + frontend + store only.
- **No Syncthing**: `/tmp/syncthing` is ephemeral and empty; `syncthing_status`
  reports "available" (directory exists) but no nodes register. This is the
  silent-second-cluster caveat from `docker/README.md:162-167`, restated.
- **Single disk**: DB and JWT key share one mount; a disk failure loses both.
- **`VITE_API_URL` is build-time**: changing the public URL needs a rebuild
  (or a runtime override in settings).
- **Free tier**: no persistent disk; use starter plan or above.
- **Submodule dependency**: if Render stops initialising submodules, the build
  fails; fallback is an external image pipeline (out of scope).

---

## 8. Risks

- **Shell-form CMD and signal handling**: mitigated by `exec` inside `sh -c`,
  so uvicorn is PID 1 and receives `SIGTERM` directly. Verify in L5 lifecycle
  (restart policy) test.
- **Healthcheck quoting**: the Python one-liner must stay valid; SWE should add
  a unit-level sanity check or rely on the compose L3 probe.
- **Render env-var → build-arg translation**: if Render does not pass
  `VITE_API_URL` as a build arg, the bundle falls back to localhost and the
  dashboard reports `BACKEND_UNREACHABLE` until the user sets it in settings.
  Document the settings-page override as the recovery path.

---

## 9. Out of Scope

- Implementing `pooled` DB pool for async (issue #130).
- Worker agent deployment story (issue #104).
- Frontend same-origin default for `VITE_API_URL` (would remove the build-time
  dependency; a candidate follow-up, not required here).
- Render-native (non-Docker) runtime.
- Custom domains / TLS (Render provides HTTPS by default on `*.onrender.com`).
