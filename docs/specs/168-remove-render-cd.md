# Spec: Totally Remove Render.com CD Infrastructure

**Issue**: #168 — "Totally remove CD to Render infrastructure"  
**Labels**: `chore`, `phase-2`, `ready-for-grooming` → `groomed`  
**Author**: PM  
**Date**: 2026-11-15  
**Baseline**: `main` (post #165, #166, #167)  
**Measured on**: Windows 11, pinned toolchain per `AGENTS.md`

---

This file is the authoritative input for the SWE cycle. It is self-sufficient; §3 carries the decisions, §4 carries the file operations. §5 is the acceptance checklist. No code is implemented by this spec.

---

## 0. Baseline, as re-measured by PM

| Gate | On `main` |
|---|---|
| `ruff check . && ruff format --check . && mypy .` | expected clean |
| `pytest --cov=backend` | expected green; compose suite skipped unless `SHC_COMPOSE_TESTS=1` |
| `cd frontend && npm run lint` | expected clean |
| `cd frontend && npm run typecheck` | expected clean (baseline empty) |
| `cd frontend && npm test` | expected green |
| Compose L1 static (`SHC_COMPOSE_TESTS=1 ... -k config`) | `docker compose config` resolves; pinned volumes `shc-data`/`shc-state` |

The changes in this spec are **purely subtractive** for Render-specific infrastructure. They must not move any of those needles: backend gates stay green, frontend gates stay green, compose suite (L1–L5) keeps passing on the checked-in `docker-compose.yaml`, and the Docker image build continues to work for self-hosted use.

---

## 1. Verified Problem Statement

### 1.1 Render Blueprint exists at repository root

`render.yaml` defines a Render web service with Docker runtime, a 1 GB disk at `/app/data`, and Render-specific env vars including `VITE_API_URL=https://scientific-home-cluster.onrender.com/api/v1`. This file is dead code — the project no longer targets Render.

### 1.2 CD workflow triggers Render deploys

`.github/workflows/cd.yml` has three jobs:
- `build-and-push`: builds the Docker image with a **Render-flavored** `VITE_API_URL` build arg (`https://${{ env.RENDER_SERVICE_NAME }}.onrender.com/api/v1`) and pushes to GHCR
- `deploy-staging`: triggers Render deploy via `render/deploy-action@v1` using `RENDER_SERVICE_ID_STAGING` and `RENDER_API_KEY` secrets
- `deploy-production`: manual-approval production deploy to Render using `RENDER_SERVICE_ID_PRODUCTION`

The workflow runs on `workflow_run` of CI on `main`. It is the only consumer of the Render secrets.

### 1.3 Documentation references Render as a deployment target

- `README.md` lines 292–347: "Deploy to Render.com" section with Blueprint path, manual path, env vars, disk config, token retrieval, and limitations
- `README.md` lines 361–384: "CD (`.github/workflows/cd.yml`)" section describing the Render deploy pipeline and required secrets
- `docker/README.md` lines 489–574: "Deploying on Render.com" section with prerequisites, Blueprint/manual paths, port/disk/`VITE_API_URL`/Syncthing docs, and limitations

### 1.4 Specs describe Render as a deployment target

- `docs/specs/153-render-deployment.md`: Full spec for Render deployment (issue #153)
- `docs/specs/154-syncthing-in-container.md`: Spec for Syncthing in container, includes Render-specific `render.yaml` modifications (issue #154)

### 1.5 What must NOT be removed

- `docker/Dockerfile`: keeps `${PORT:-8000}` binding and `HEALTHCHECK` using `PORT` — this is generic, not Render-specific
- `docker-compose.yaml`: single-container self-hosted deployment, unchanged
- `docker/entrypoint.sh`: Syncthing daemon + uvicorn entrypoint, unchanged
- CI workflow (`.github/workflows/ci.yml`): four jobs `backend-gates`, `frontend-gates`, `compose-suite`, `e2e` — all required status checks on `main`
- GHCR image build: the `build-and-push` job in `cd.yml` builds a usable image; the question is whether to keep GHCR publishing without Render deploys (decision D2)

---

## 2. Root Cause / Constraints (summary)

The project pivoted from Render (a managed PaaS with no GPU workers) to self-hosted Docker + portable binaries (issues #165, #166). Render infrastructure is now dead code that causes confusion, keeps secrets referenced, and risks accidental deploys to a stale service. The removal is purely subtractive; the container image, compose file, and CI gates must remain fully functional for self-hosted use.

---

## 3. Decisions

### D1 — Delete `render.yaml` entirely

No replacement. The file is not referenced by any remaining workflow or documentation.

### D2 — Rewrite `.github/workflows/cd.yml` to **only** build and push to GHCR

**Keep**: the `build-and-push` job (renamed to `build-and-publish`), which produces a tagged Docker image at `ghcr.io/<owner>/<repo>:<sha>` and `:latest`.

**Remove**: `deploy-staging` and `deploy-production` jobs entirely.

**Change**: the `VITE_API_URL` build arg in `build-and-push` from the Render-specific URL to a generic placeholder that works for self-hosted use. The image must be buildable without knowing the deployment URL — operators set `VITE_API_URL` at deploy time (via `.env` or build arg) or override at runtime via the settings page. Use the same default as the Dockerfile: `http://localhost:8000/api/v1`.

**Rationale**: GHCR publishing is valuable for operators who want to `docker pull` a prebuilt image rather than building locally. The CD workflow becomes a pure "build and publish" pipeline. The Render secrets (`RENDER_API_KEY`, `RENDER_SERVICE_ID_STAGING`, `RENDER_SERVICE_ID_PRODUCTION`) become unused and can be removed from GitHub repository settings by the operator.

**Trigger**: keep `workflow_run` on CI success on `main` — the image is published automatically after green CI.

**Permissions**: keep `packages: write` for GHCR push.

### D3 — Remove all Render documentation from `README.md`

Delete lines 292–347 ("Deploy to Render.com" section) and lines 361–384 ("CD (`.github/workflows/cd.yml`)" section).

The "CI/CD Pipeline" subsection should be renamed to "CI Pipeline" and describe only the four CI jobs. The CD workflow no longer exists in a form that deploys anywhere; it only publishes to GHCR.

### D4 — Remove all Render documentation from `docker/README.md`

Delete lines 489–574 ("Deploying on Render.com" section entirely).

### D5 — Update specs to mark Render support as removed

**Option A (preferred)**: Add a prominent banner at the top of `docs/specs/153-render-deployment.md` and `docs/specs/154-syncthing-in-container.md` stating: "**REMOVED**: Render.com deployment support was removed in issue #168. This spec is retained for historical reference only."

**Option B**: Move both files to `docs/specs/archived/` (create if needed) and add a note in the main `docs/specs/README.md` (or create one) linking to them.

Decision: **Option A** — keeps git history intact and avoids broken links from other docs/issues.

### D6 — No changes to Dockerfile, entrypoint, compose, or CI

- `docker/Dockerfile`: `${PORT:-8000}` and `HEALTHCHECK` using `PORT` are generic and stay
- `docker/entrypoint.sh`: unchanged
- `docker-compose.yaml`: unchanged
- `.github/workflows/ci.yml`: unchanged (four required jobs stay as-is)

---

## 4. File Operations

### 4.1 Files to DELETE

| File | Reason |
|---|---|
| `render.yaml` | Render Blueprint — dead code |
| `docs/specs/153-render-deployment.md` | **Option A**: keep with "REMOVED" banner (see D5). **Option B**: move to archive. This spec assumes Option A — banner added, file kept. |
| `docs/specs/154-syncthing-in-container.md` | **Option A**: keep with "REMOVED" banner. File kept. |

> **Note**: The spec files are not deleted; they are annotated. This preserves git history and avoids breaking links from issues #153, #154, PRs, and discussions.

### 4.2 Files to MODIFY

| File | Changes |
|---|---|
| `.github/workflows/cd.yml` | **Rewrite**: Keep only `build-and-publish` job (renamed from `build-and-push`). Remove `deploy-staging` and `deploy-production`. Change `VITE_API_URL` build arg to `http://localhost:8000/api/v1`. Update job name, step names, and comments to reflect "build and publish to GHCR only". |
| `README.md` | **Delete lines 292–347** ("Deploy to Render.com" section). **Delete lines 361–384** ("CD (`.github/workflows/cd.yml`)" section). Rewrite "CI/CD Pipeline" → "CI Pipeline" describing only the four CI jobs. |
| `docker/README.md` | **Delete lines 489–574** ("Deploying on Render.com" section entirely). |
| `docs/specs/153-render-deployment.md` | **Add banner** at top (after front matter): `> **REMOVED**: Render.com deployment support was removed in issue #168. This spec is retained for historical reference only.` |
| `docs/specs/154-syncthing-in-container.md` | **Add banner** at top (after front matter): `> **REMOVED**: Render.com deployment support was removed in issue #168. This spec is retained for historical reference only.` |

### 4.3 Files UNCHANGED (verify they still work)

- `docker/Dockerfile`
- `docker/entrypoint.sh`
- `docker-compose.yaml`
- `.github/workflows/ci.yml`
- `docker/Dockerfile` build args defaults (`VITE_API_URL=http://localhost:8000/api/v1`, `VITE_CLUSTER_BACKEND=http`)

---

## 5. Acceptance Criteria

- [ ] **AC-1** `render.yaml` does not exist at the repository root.
- [ ] **AC-2** `.github/workflows/cd.yml` contains only a `build-and-publish` job that builds the Docker image and pushes to GHCR; no `deploy-staging`, no `deploy-production`, no `render/deploy-action`.
- [ ] **AC-3** `cd.yml` uses `VITE_API_URL=http://localhost:8000/api/v1` (the Dockerfile default) as the build arg, not a Render-specific URL.
- [ ] **AC-4** `README.md` has no "Deploy to Render.com" section and no "CD" section describing Render deploys. The "CI Pipeline" section describes only the four CI jobs.
- [ ] **AC-5** `docker/README.md` has no "Deploying on Render.com" section.
- [ ] **AC-6** `docs/specs/153-render-deployment.md` and `docs/specs/154-syncthing-in-container.md` each have a "REMOVED" banner at the top noting issue #168.
- [ ] **AC-7** `docker/Dockerfile` still uses `${PORT:-8000}` in `CMD` and `HEALTHCHECK`; `EXPOSE 8000` unchanged.
- [ ] **AC-8** `docker-compose.yaml` unchanged; `docker compose config` resolves; pinned volumes `shc-data`/`shc-state` unchanged.
- [ ] **AC-9** Backend gates pass: `ruff check . && ruff format --check . && mypy .` clean; `pytest --cov=backend` green.
- [ ] **AC-10** Frontend gates pass: `cd frontend && npm run lint` clean; `cd frontend && npm run typecheck` clean; `cd frontend && npm test` green.
- [ ] **AC-11** Compose suite passes: `SHC_COMPOSE_TESTS=1 pytest backend/tests/compose -m compose -v` green (L1–L5).
- [ ] **AC-12** Docker image builds successfully: `docker build -f docker/Dockerfile -t shc:test .` succeeds; container starts and serves `/api/v1/health` on port 8000.
- [ ] **AC-13** No references to `RENDER_API_KEY`, `RENDER_SERVICE_ID_STAGING`, `RENDER_SERVICE_ID_PRODUCTION` remain in any tracked file.
- [ ] **AC-14** CI on `main` stays green after removal (all four required jobs pass).

---

## 6. Test Scenarios

### 6.1 Local verification (no Docker daemon required for most)

```bash
# Verify render.yaml is gone
test ! -f render.yaml && echo "OK: render.yaml removed"

# Verify cd.yml has no Render deploy jobs
! grep -q "deploy-staging\|deploy-production\|render/deploy-action" .github/workflows/cd.yml && echo "OK: no Render deploy jobs"

# Verify cd.yml uses localhost build arg
grep -q 'VITE_API_URL=http://localhost:8000/api/v1' .github/workflows/cd.yml && echo "OK: localhost build arg"

# Verify README.md has no Render sections
! grep -q "Deploy to Render.com" README.md && echo "OK: no Render deploy section in README"
! grep -q "CD (.\.github/workflows/cd.yml)" README.md && echo "OK: no CD section in README"

# Verify docker/README.md has no Render section
! grep -q "Deploying on Render.com" docker/README.md && echo "OK: no Render section in docker/README"

# Verify specs have REMOVED banner
grep -q "REMOVED.*Render.com deployment support was removed in issue #168" docs/specs/153-render-deployment.md && echo "OK: 153 banner present"
grep -q "REMOVED.*Render.com deployment support was removed in issue #168" docs/specs/154-syncthing-in-container.md && echo "OK: 154 banner present"

# Verify no Render secrets referenced
! grep -qr "RENDER_API_KEY\|RENDER_SERVICE_ID_STAGING\|RENDER_SERVICE_ID_PRODUCTION" --include="*.md" --include="*.yml" --include="*.yaml" . && echo "OK: no Render secrets referenced"

# Verify Dockerfile PORT handling intact
grep -q '\${PORT:-8000}' docker/Dockerfile && echo "OK: PORT fallback in CMD"
grep -q "os.environ.get('PORT'" docker/Dockerfile && echo "OK: PORT fallback in HEALTHCHECK"
```

### 6.2 Docker build & run (requires Docker daemon)

```bash
# Build image with default args
docker build -f docker/Dockerfile -t shc:render-removal-test .

# Run container on default port 8000
docker run -d --name shc-test -p 8000:8000 -v shc-test-data:/app/data -v shc-test-state:/app/.shc shc:render-removal-test

# Wait for health
for i in {1..30}; do curl -sf http://localhost:8000/api/v1/health && break; sleep 2; done

# Verify health returns 200 and healthy status
curl -sf http://localhost:8000/api/v1/health | jq -e '.status == "healthy"'

# Cleanup
docker rm -f shc-test
docker volume rm shc-test-data shc-test-state
```

### 6.3 Compose regression (opt-in, destructive-adjacent)

```bash
SHC_COMPOSE_TESTS=1 SHC_COMPOSE_ALLOW_DESTRUCTIVE=1 pytest backend/tests/compose -m compose -v
```

### 6.4 Full gate verification

```bash
# Backend gates (from repo root)
ruff check . && ruff format --check . && mypy .
pytest --cov=backend

# Frontend gates
cd frontend && npm run lint && npm run typecheck && npm test
```

---

## 7. Breaking Changes / Migration Notes

| Area | Change | Migration |
|---|---|---|
| **Render Blueprint** | `render.yaml` deleted | Operators using Render must migrate to self-hosted Docker (`docker compose up -d`) or portable binary (`scripts/build_app.py`). No automated migration. |
| **CD Pipeline** | Render deploy jobs removed; only GHCR publish remains | GitHub secrets `RENDER_API_KEY`, `RENDER_SERVICE_ID_STAGING`, `RENDER_SERVICE_ID_PRODUCTION` are now unused — operator should remove them from repository settings. |
| **Documentation** | Render setup docs removed | Self-hosted Docker docs in `README.md` "Docker Compose" section and `docker/README.md` are the canonical deployment guides. |
| **Specs** | Historical specs annotated | No action needed; specs are reference only. |
| **Image build arg** | `VITE_API_URL` default in CD is now `http://localhost:8000/api/v1` | Operators building via GHCR-published image must override `VITE_API_URL` at deploy time (`.env`, build arg, or settings page). This was already required for non-Render deployments. |

**No breaking changes** to:
- `docker/Dockerfile` (PORT handling unchanged)
- `docker-compose.yaml` (unchanged)
- `docker/entrypoint.sh` (unchanged)
- CI workflow (unchanged)
- Backend/frontend source code (no changes)

---

## 8. Risks

- **GHCR publish without deploy**: The rewritten `cd.yml` publishes to GHCR but deploys nowhere. This is intentional — operators `docker pull` from GHCR and run locally. If GHCR publishing is not desired, the entire `cd.yml` can be deleted in a follow-up (out of scope).
- **Stale Render secrets in GitHub**: The three Render secrets become unused but are not automatically removed. Document in PR description that the operator should delete them.
- **Links from issues/PRs**: Issues #153, #154 and related PRs link to the spec files. The "REMOVED" banner makes the status clear without breaking links.
- **CI/CD confusion**: The workflow is still named "CD" but only publishes. Consider renaming to "Publish" in a follow-up (out of scope).

---

## 9. Out of Scope

- Renaming `cd.yml` to `publish.yml` or similar
- Deleting `cd.yml` entirely (GHCR publish is kept per D2)
- Removing Render secrets from GitHub (operator action, not code)
- Migrating any existing Render deployment (operator action)
- Changing the Dockerfile's PORT handling (it is generic, not Render-specific)
- Any backend/frontend code changes

---

## 10. Rollback Plan

If any gate fails after the change:
1. Revert the commit(s) on `main`
2. The removed files (`render.yaml`, spec banners) are recoverable from git history
3. The rewritten `cd.yml` can be restored from git history
4. Documentation sections can be restored from git history

No database migrations, no state changes, no irreversible operations.