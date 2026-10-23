# Spec: Run Syncthing Daemon in the Container (Render + Docker)

**Issue**: #154 — "Run Syncthing daemon in the container (Render + Docker)"
**Labels**: `enhancement`, `phase-3`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-23
**Baseline**: `main` (post #153)
**Measured on**: Windows 11, pinned toolchain per `AGENTS.md`

**This file is the authoritative input for the SWE cycle.** It is self-sufficient:
§3 carries the decisions, §4 carries the code shapes. §5 is the acceptance
checklist. No code is implemented by this spec.

---

## 0. Baseline, as re-measured by PM

| Gate | On `main` |
|---|---|
| `ruff check . && ruff format --check . && mypy .` | expected clean |
| `pytest --cov=backend` | expected green; compose suite skipped unless `SHC_COMPOSE_TESTS=1` |
| Compose L1 static (`SHC_COMPOSE_TESTS=1 ... -k config`) | `docker compose config` resolves; pinned volumes `shc-data`/`shc-state`; one published port `8000` |

The changes in this spec must not move any of those needles: backend gates stay
green, `backend/tests/compose/` (L1–L5) keeps passing on the checked-in
`docker-compose.yaml`, and no new Python dependencies are introduced. The new
`docker/entrypoint.sh` and the Syncthing binary are additive to the image and do
not change the compose file's resolved shape.

---

## 1. Verified Problem Statement

### 1.1 The daemon is absent

Issue #153 made the container run on Render (`PORT`, single disk, co-located
state) but explicitly left Syncthing out: `/tmp/syncthing` is created at
startup by `backend/services/syncthing_service.py:190-193`, so
`syncthing_status` reports `"available"` (the directory exists) while no
Syncthing process exists and no node ever registers. That is issue #104's
silent second cluster, restated for a PaaS.

### 1.2 The image has no Syncthing binary

`docker/Dockerfile` (final stage `python:3.11-slim-bookworm`) installs only the
Python package. There is no `syncthing` on `PATH`, no config, and no state
directory beyond what the backend creates for its own watcher.

### 1.3 The container runs a single process

`docker/Dockerfile` ends with `CMD ["sh", "-c", "exec uvicorn ..."]` (post #153).
There is no process supervisor, no second service, and no entrypoint hook. The
backend is PID 1. Adding a daemon means either a supervisor or a backgrounded
process plus an `exec` of uvicorn, per the issue's proposed scope.

### 1.4 Syncthing state is XML and identity-bearing

Syncthing persists its device identity (`cert.pem`/`key.pem`), its
`config.xml`, and its database under a single "home" directory (commonly
`~/.config/syncthing` or `$STHOMEDIR`). That home directory must survive a
container replace or every replace generates a new device identity and breaks
pairing. The device ID is derived from the cert, so the home directory is the
unit of persistence — not individual files.

### 1.5 Config must name the synced folder

A freshly generated Syncthing home contains a default folder pointing at
`~/Sync`. This project needs the default folder to point at `SYNCTHING_ROOT`
(`/tmp/syncthing` in the container, which is the bind mount on a Docker host
and an ephemeral dir on Render). The folder path has to be patched into
`config.xml` after generation.

### 1.6 Syncthing's management surface is local-only by design

The GUI listens on `127.0.0.1:8384` and the sync protocol on `0.0.0.0:22000`
(TCP + QUIC/UDP) plus local discovery on UDP 21027. Render exposes exactly one
public port (`PORT`, the backend), so the GUI and sync protocol are not
publicly reachable there. On a Docker host they are reachable only if the
operator publishes them. This is a documentation/limitation item, not a code
change to the backend.

### 1.7 The compose suite pins the image's shape

`backend/tests/compose/test_image_content.py` (M-2.3) asserts `node`/`npm`
absent and `_shell.html` present, and M-2.3's successor assertions (S-3) assert
`ExposedPorts == {"8000/tcp"}` and `Volumes == {"/app/data", "/app/.shc"}`.
`test_compose_config.py` (M-1.8) asserts exactly one published port (`8000`).
Any change that adds published ports or exposed ports breaks those gates, so
this spec keeps them untouched and documents Syncthing's ports separately.

---

## 2. Root Cause / Constraints (summary)

The container is an API + frontend + store artifact with no daemon. Making it
participate in a real cluster requires (a) the Syncthing binary in the image,
(b) a persistent home directory on the single available disk/volume, (c) a
generated-and-patched `config.xml` so the default folder is `SYNCTHING_ROOT`,
and (d) a startup sequence that runs the daemon alongside uvicorn. Nothing in
the backend needs to change for correctness; the gap is in the image and its
entrypoint. Compose and Render env contracts must survive unchanged.

---

## 3. Decisions

### D1 — Install Syncthing from the official static tarball

The binary comes from the GitHub release asset `syncthing-linux-<arch>-v<ver>.tar.gz`,
extracted and installed to `/usr/local/bin/syncthing`. No apt repository, no
distro package (which lags and differs between Debian versions). `curl` and
`ca-certificates` are installed via apt only to fetch the tarball; they are
build-time tools and the final image keeps them (acceptable; the Node/npm
absence asserted by M-2.3 is unaffected).

A pinned version is mandatory for reproducible builds:

```dockerfile
ARG SYNCTHING_VERSION=1.27.12
ARG TARGETARCH=amd64
```

SWE verifies the default against
`https://github.com/syncthing/syncthing/releases` at implementation time and
bumps it if the tag 404s; the exact value is not asserted by any test. The
architecture is taken from Docker's `TARGETARCH` (with an `amd64` default so a
plain `docker build` on an amd64 host without BuildKit still works), mapped 1:1
to Syncthing's arch names (`amd64`, `arm64`, `arm`, `386`).

**Checksum verification**: the release's `sha256sums.txt` should be fetched and
the tarball verified when the implementation is able to do so cheaply; if the
release layout makes that fragile, SWE may ship without verification and note
it. Not a gate either way.

**Rejected**: downloading "latest" without a version. GitHub's release-asset URLs
include the version, so there is no stable unversioned tarball URL.

### D2 — Syncthing home lives under the data disk/volume

The home directory is configurable via `SYNCTHING_HOME`, defaulting to
`/app/data/syncthing`. That path is inside `SHC_DATA_DIR` (`/app/data`),
which is the single Render Disk mount and the `shc-data` named volume on
Docker. Consequences:

- Render: state persists on the disk across deploys/restarts.
- Docker/compose: state persists in `shc-data`, alongside the SQLite database.
- No second volume or disk is required, which is what makes this work on
  Render's one-disk-per-service limit.

`STHOMEDIR` is exported to the same value for any subprocess that expects the
native variable, but the entrypoint always passes `-home` explicitly so there
is one source of truth.

`SYNCTHING_ROOT` is **not** moved: it stays the synced folder (`/tmp/syncthing`
in the container), distinct from the home directory. The home holds identity and
config; the root holds jobs/nodes YAML. Confusing the two would put the Syncthing
database inside the watched folder.

### D3 — Entrypoint: generate, patch, background, then `exec` uvicorn

A shell entrypoint (`docker/entrypoint.sh`, installed to
`/usr/local/bin/entrypoint.sh`) replaces the Dockerfile's `CMD`:

1. Resolve `SYNCTHING_HOME`, `SYNCTHING_ROOT`, `PORT` (default `8000`).
2. `mkdir -p` both.
3. If `config.xml` is absent, run `syncthing -generate="$SYNCTHING_HOME"` to
   create the identity and a default config.
4. On first run only (marker file `.shc-configured`), patch `config.xml`:
   - every `<folder>`'s `path` attribute → `SYNCTHING_ROOT`;
   - `<autoAcceptFolders>` → `true` in `<options>`;
   - `<gui><address>` → `127.0.0.1:8384`.
   Then write the marker so later edits by the operator are preserved.
5. Export `STNOUPGRADE=1` so Syncthing cannot replace its own binary at runtime.
6. Start `syncthing -home=... -no-browser -no-restart` in the background,
   redirecting output to `$SYNCTHING_HOME/syncthing.log`.
7. `exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"` so uvicorn
   becomes PID 1 and receives `SIGTERM` directly.

`set -euo pipefail` makes a failed generation abort the container rather than
silently starting with no daemon.

**Rejected**: a process supervisor (supervisord, s6). It would make Syncthing a
managed child and improve reaping, but it adds a dependency and a second PID-1
contract for a single background process. The issue explicitly asks for "starts
Syncthing in the background, then execs uvicorn as PID 1", which is what this
does. The orphan/zombie cost on `docker stop` is recorded as a risk (§8).

### D4 — Minimal auto-generated config, patched in Python

Syncthing's config is XML and its device ID is derived from the generated cert,
so a static template cannot carry the identity. The pragmatic path is
`syncthing -generate` followed by an idempotent Python patch using
`xml.etree.ElementTree`. The patch is applied only once (marker file) so an
operator who edits `config.xml` by hand keeps their edits across restarts;
deleting the marker re-applies. `autoAcceptFolders=true` auto-accepts *folders*
offered by an already-paired device; it does **not** auto-accept new devices,
which is the unsafe half and is deliberately left off.

### D5 — `SYNCTHING_ROOT` stays the backend's value

`backend/core/config.py` already defaults `SYNCTHING_ROOT=/tmp/syncthing` and
the health check reports `"available"` when that directory exists. No Python
change is required: the daemon now actually populates it. The backend's
watcher continues to scan `jobs/` and `nodes/` under it.

### D6 — Ports are documented, not published

Syncthing listens on:

- `127.0.0.1:8384` — GUI / REST (loopback only, not reachable from outside
  the container).
- `0.0.0.0:22000/tcp` and `0.0.0.0:22000/udp` — sync protocol (TCP + QUIC).
- `0.0.0.0:21027/udp` — local discovery.

`docker-compose.yaml` keeps publishing **only** `8000:8000` (M-1.8). Operators
who want cross-node sync on a Docker host publish `22000:22000/tcp`,
`22000:22000/udp` and (optionally) `21027:21027/udp` themselves. Render
cannot publish these (single public port) and cannot run GPU workers, so a
Render deployment remains API + frontend + store + a local Syncthing daemon
that has no peers — documented as a limitation, not a defect. No change to
`EXPOSE` is made, to keep S-3's `ExposedPorts == {"8000/tcp"}` assertion
green; the ports are listed in the README instead.

### D7 — No compose changes

`docker-compose.yaml` is untouched. The Syncthing home defaults to
`/app/data/syncthing`, which is inside the already-mounted `shc-data` volume,
so state persists without a new mount and the L1 static assertions (one
service, pinned volumes, one published port, `SYNCTHING_ROOT` literal) survive.
`SHC_COMPOSE_TESTS` gates are unaffected.

### D8 — Render blueprint gains `SYNCTHING_HOME`

`render.yaml` sets `SYNCTHING_HOME=/app/data/syncthing` explicitly (for
documentation and to make the env var visible in the dashboard), even though the
image default already resolves there. `SYNCTHING_ROOT` stays `/tmp/syncthing`.
The disk at `/app/data` therefore holds the database, the JWT key, and the
Syncthing identity/config/database together.

### D9 — Auto-upgrade disabled

`STNOUPGRADE=1` is exported in the entrypoint. A container image is immutable;
letting Syncthing replace `/usr/local/bin/syncthing` at runtime would be lost
on the next replace and would diverge from the pinned version.

### D10 — Health check unchanged

`/api/v1/health` continues to report `"available"` when `SYNCTHING_ROOT`
exists. The backend does not probe the daemon; the directory's existence is the
contract (unchanged from #153). Render's health check stays
`/api/v1/health`.

---

## 4. Code Shapes

### 4.1 `docker/Dockerfile` (modify)

Add after the backend install / before the frontend copy (or anywhere in the
runtime stage), the Syncthing install:

```dockerfile
# Syncthing static build (issue #154). No apt repository: the binary is
# extracted from the official release tarball. curl + ca-certificates are the
# only apt packages added, for fetching it.
ARG SYNCTHING_VERSION=1.27.12
ARG TARGETARCH=amd64

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && case "$TARGETARCH" in \
        amd64|arm64|arm|386) ARCH="$TARGETARCH" ;; \
        *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL \
        "https://github.com/syncthing/syncthing/releases/download/v${SYNCTHING_VERSION}/syncthing-linux-${ARCH}-v${SYNCTHING_VERSION}.tar.gz" \
        -o /tmp/syncthing.tar.gz \
 && tar -xzf /tmp/syncthing.tar.gz -C /tmp \
 && install -m 0755 "/tmp/syncthing-linux-${ARCH}-v${SYNCTHING_VERSION}/syncthing" /usr/local/bin/syncthing \
 && rm -rf /tmp/syncthing* \
 && syncthing --version
```

Then install the entrypoint and switch the command:

```dockerfile
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# (HEALTHCHECK block unchanged from #153, reading PORT with an 8000 fallback.)

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

Remove the previous `CMD ["sh", "-c", "exec uvicorn ..."]` (the entrypoint now
owns port binding and the background daemon). `EXPOSE 8000` stays as-is.

No change to `VOLUME ["/app/data", "/app/.shc"]` or the baked env block
(`SERVE_FRONTEND`, `FRONTEND_DIR`, `SHC_DATA_DIR`, `PYTHONPATH`, `DB_POOL`).
`SYNCTHING_HOME` is deliberately **not** baked: the entrypoint default covers
it and an operator can override without rebuilding.

### 4.2 `docker/entrypoint.sh` (new)

```sh
#!/usr/bin/env bash
set -euo pipefail

SYNCTHING_HOME="${SYNCTHING_HOME:-/app/data/syncthing}"
SYNCTHING_ROOT="${SYNCTHING_ROOT:-/tmp/syncthing}"
PORT="${PORT:-8000}"

mkdir -p "$SYNCTHING_HOME" "$SYNCTHING_ROOT"

export STNOUPGRADE=1

# First run: generate identity + default config.
if [ ! -f "$SYNCTHING_HOME/config.xml" ]; then
    syncthing -generate="$SYNCTHING_HOME" >/dev/null 2>&1
fi

# First-run tuning: applied once, then a marker prevents clobbering operator
# edits on later restarts. Delete the marker to re-apply.
if [ ! -f "$SYNCTHING_HOME/.shc-configured" ] && [ -f "$SYNCTHING_HOME/config.xml" ]; then
    python3 - "$SYNCTHING_HOME/config.xml" "$SYNCTHING_ROOT" <<'PY'
import sys
import xml.etree.ElementTree as ET

cfg_path, root = sys.argv[1], sys.argv[2]
tree = ET.parse(cfg_path)
cfg = tree.getroot()

for folder in cfg.findall("folder"):
    folder.set("path", root)

for opts in cfg.findall("options"):
    el = opts.find("autoAcceptFolders")
    if el is None:
        el = ET.SubElement(opts, "autoAcceptFolders")
    el.text = "true"

for gui in cfg.findall("gui"):
    addr = gui.find("address")
    if addr is None:
        addr = ET.SubElement(gui, "address")
    addr.text = "127.0.0.1:8384"

tree.write(cfg_path, encoding="utf-8", xml_declaration=True)
PY
    touch "$SYNCTHING_HOME/.shc-configured"
fi

# Background daemon. Logs go to the persisted home so they survive a replace
# for diagnosis (they are not the unit of persistence, but they are cheap).
syncthing -home="$SYNCTHING_HOME" -no-browser -no-restart \
    >"$SYNCTHING_HOME/syncthing.log" 2>&1 &

exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
```

### 4.3 `render.yaml` (modify)

Add `SYNCTHING_HOME` to `envVars` (alongside the existing `SYNCTHING_ROOT`):

```yaml
      - key: SYNCTHING_HOME
        value: /app/data/syncthing
```

No disk change: the existing disk at `/app/data` already covers it. `PORT` is
still not listed (Render injects it).

### 4.4 `docker/README.md` (modify — add subsections)

Add to the existing "Deploying on Render.com" / limitations area and to the
configuration table:

- New env var row: `SYNCTHING_HOME` (default `/app/data/syncthing`) —
  Syncthing identity, config and database. Must be on persistent storage.
- A "Syncthing daemon" subsection documenting:
  - The image runs `syncthing` as a background process started by the
    entrypoint; uvicorn is PID 1.
  - State persists under `SYNCTHING_HOME` (disk/volume).
  - Default folder is `SYNCTHING_ROOT` (`/tmp/syncthing`).
  - Auto-accept folders is on; auto-accept devices is **not** (pair devices
    manually or via an introducer).
  - Ports: GUI `127.0.0.1:8384` (loopback, not published); sync
    `22000/tcp` + `22000/udp`; local discovery `21027/udp`. To sync between
    Docker hosts, publish `22000` (tcp+udp) and optionally `21027/udp` in your
    own compose override; the checked-in file publishes only `8000`.
  - Auto-upgrade is disabled; bump `SYNCTHING_VERSION` and rebuild to upgrade.
  - Render limitations: GUI and sync port are not publicly reachable; a Render
    deployment's daemon has no peers and cannot accept device connections from
    the public internet. Jobs cannot run on Render (no workers) regardless.

Update the Render "Limitations" list to replace "No Syncthing" with the more
accurate "Syncthing runs locally but is not reachable for pairing (single public
port); no peers register on Render."

### 4.5 Backend / config (no code change required)

No Python changes. `SYNCTHING_ROOT` and the health check are unchanged.
Optional (nice-to-have, not required for AC): a one-line comment in
`backend/core/config.py` noting that `SYNCTHING_HOME` is consumed by the
container entrypoint rather than by the backend. Defer unless SWE wants it.

### 4.6 Compose test expectations (no change, but SWE should be aware)

- M-2.3 (`node`/`npm` absent, `_shell.html` present) still holds; `syncthing`
  is now an additional binary on `PATH`, which no existing assertion forbids.
  SWE may add a positive assertion (`command -v syncthing`) to M-2.3 as part of
  this issue; that is a test edit, not a gate regression.
- S-3 (`ExposedPorts == {"8000/tcp"}`, `Volumes == {"/app/data", "/app/.shc"}`)
  still holds because this spec deliberately does not add `EXPOSE` lines or
  volumes. If SWE chooses to expose Syncthing ports, S-3 must be updated in the
  same change.
- M-1.8 (exactly one published port, `8000`) still holds because
  `docker-compose.yaml` is untouched.

---

## 5. Acceptance Criteria

- [ ] **AC-1** Image contains `syncthing` on `PATH` (`command -v syncthing`
      succeeds in a `docker run`).
- [ ] **AC-2** Container starts both Syncthing and the backend; the backend
      reaches `healthy` and `/api/v1/health` returns 200.
- [ ] **AC-3** On first start, `$SYNCTHING_HOME/config.xml` is generated and
      its default folder's `path` is `$SYNCTHING_ROOT`; `autoAcceptFolders` is
      `true`; the GUI address is `127.0.0.1:8384`.
- [ ] **AC-4** Syncthing state (`config.xml`, `cert.pem`, `key.pem`,
      database) persists across a container replace when `SYNCTHING_HOME` is
      on a volume/disk.
- [ ] **AC-5** `STNOUPGRADE=1` is in effect (Syncthing does not attempt to
      upgrade itself).
- [ ] **AC-6** Documentation covers ports (8384, 22000/tcp+udp, 21027/udp),
      pairing, auto-accept scope, and limitations (Render reachability, no
      workers).
- [ ] **AC-7** `render.yaml` sets `SYNCTHING_HOME=/app/data/syncthing`; the
      disk at `/app/data` persists it.
- [ ] **AC-8** Backend gates pass: `ruff check . && ruff format --check . &&
      mypy .` clean; `pytest --cov=backend` green.
- [ ] **AC-9** Compose gates pass: `backend/tests/compose/` (L1–L5) still
      pass; `docker compose config` resolves; pinned volumes and single published
      port unchanged.
- [ ] **AC-10** No new Python dependencies; no changes to `backend/` source
      required.

---

## 6. Testing

### 6.1 Local (Docker daemon required)

- `docker build -f docker/Dockerfile -t shc:syncthing .` succeeds (network
  required for the tarball).
- `docker run --rm shc:syncthing sh -c 'command -v syncthing && syncthing --version'`
  prints a version.
- `docker run -d --name shc-st -p 8000:8000 -v shc-st-data:/app/data shc:syncthing`
  then `curl http://127.0.0.1:8000/api/v1/health` returns 200 within the
  start period.
- `docker exec shc-st test -f /app/data/syncthing/config.xml` succeeds and
  `grep`/XML inspection shows `path="/tmp/syncthing"` and
  `autoAcceptFolders` true.
- `docker exec shc-st pgrep -a syncthing` shows a running daemon.
- Restart/replace (`docker rm -f shc-st` then re-run with the same volume)
  keeps the same `cert.pem`/`key.pem` (device identity stable) and does not
  regenerate `config.xml` (marker present).

### 6.2 Compose regression

- `SHC_COMPOSE_TESTS=1 pytest backend/tests/compose -m compose -v` (and the
  destructive scenario with `SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`) remain green.
  The compose file is untouched; the Dockerfile's command change (from a
  `sh -c "exec uvicorn"` CMD to an `ENTRYPOINT` script that ends in the same
  `exec`) must not break L3 health or L5 lifecycle. Verify `(healthy)`.

### 6.3 Render (manual, out of CI)

- Import/redeploy the Blueprint; build succeeds (submodule present, tarball
  fetched).
- First deploy: health check green; Render Shell shows
  `/app/data/syncthing/config.xml` and `syncthing.log`.
- Redeploy: config and identity persist (disk works).
- Document that no peers register and jobs stay pending (expected).

---

## 7. Limitations (documented, not fixed)

- **Render reachability**: the GUI (8384) and sync protocol (22000) are not
  publicly reachable on Render (single public port). The daemon runs but has no
  peers; pairing requires an externally reachable Syncthing instance or
  out-of-band config exchange.
- **No worker agents**: Render cannot run GPU workloads; jobs stay pending.
- **Single disk**: DB, JWT key and Syncthing identity share `/app/data`; a
  disk failure loses all three.
- **Auto-accept scope**: only folders are auto-accepted. New devices must be
  introduced manually or via an introducer; auto-accepting devices is
  deliberately not enabled.
- **Build-time network**: the image build fetches the tarball from GitHub; an
  air-gapped build must mirror it.
- **Pinned version**: upgrading Syncthing means bumping `SYNCTHING_VERSION`
  and rebuilding; runtime auto-upgrade is disabled.
- **Signal handling**: on `docker stop`, `SIGTERM` goes to uvicorn (PID 1)
  only; the backgrounded Syncthing is orphaned and reaped by the container
  runtime when it exits. No zombie handling is added (see risk).
- **Config patching is version-sensitive**: the ElementTree patch assumes the
  generated `config.xml` shape. A future Syncthing release that renames
  `autoAcceptFolders` or the `folder/@path` attribute would silently no-op;
  SWE's tests should assert the patched values.

---

## 8. Risks

- **Tarball URL / version drift**: if `SYNCTHING_VERSION` 404s, the build
  fails. SWE verifies the default during implementation and the value is easy to
  bump.
- **Architecture detection**: `TARGETARCH` is provided by BuildKit. The
  `ARG TARGETARCH=amd64` default covers plain `docker build` on amd64; an
  arm64 host building without BuildKit would incorrectly fetch the amd64 binary.
  Document that builds must use BuildKit (the default since Docker 23).
- **Backgrounded process and PID 1**: uvicorn is PID 1 and does not reap
  zombies. Syncthing is long-lived and does not fork workers in a way that
  produces zombies in practice, but if it does they accumulate. Acceptable for
  this scope; a future hardening could add `tini` as PID 1.
- **First-run timing**: generating the identity and starting Syncthing can take
  several seconds. The health check's `--start-period=10s` (unchanged) may be
  tight on slow disks; SWE should observe during testing and only raise it if
  needed (raising it requires updating S-3).
- **Config patch idempotency**: the marker file is the guard. If an operator
  deletes `config.xml` but leaves the marker, the patch will not re-run. The
  marker is removed together with the home directory on a volume wipe, which is
  the expected recovery path.

---

## 9. Out of Scope

- Implementing `pooled` DB pool for async (issue #130).
- Worker agent deployment story (issue #104).
- Publishing Syncthing ports in the checked-in `docker-compose.yaml` (would
  break M-1.8; operators publish them in their own override).
- Adding `EXPOSE` lines for Syncthing ports (would break S-3 unless updated).
- Backend probing the Syncthing REST API for health (the directory contract
  is unchanged).
- A process supervisor (supervisord/s6) for the daemon.
- Custom domains / TLS on Render.
- Air-gapped mirror of the Syncthing tarball.