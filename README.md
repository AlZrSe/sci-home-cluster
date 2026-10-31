# Scientific Home Cluster

A distributed platform for running scientific long-running applications on various computers connected to the internet.

## Project Status

| Component | Status | Description |
|-----------|--------|-------------|
| **Frontend Dashboard** | ✅ **Complete** | React + TypeScript + Vite + Tailwind (see `frontend/`) |
| Project Setup | ✅ **Done** | Issue [#1](https://github.com/AlZrSe/sci-home-cluster/issues/1) |
| Shared Schemas & File Ops | ✅ **Done** | Issue [#2](https://github.com/AlZrSe/sci-home-cluster/issues/2) |
| API Server Core | ✅ **Done** | Issue [#3](https://github.com/AlZrSe/sci-home-cluster/issues/3) |
| Syncthing Sync Service | ✅ **Done** | Issue [#4](https://github.com/AlZrSe/sci-home-cluster/issues/4) |
| Worker Agent | ✅ **Skeleton Delivered** | Issues #93-#106 - config, logging, supervised loop, job execution, command files. See agent/README.md |
| CLI | ✅ **Stub Implemented** | Phase 2 - commands scaffolded, needs API integration |
| Docker/Production | ✅ **Done** | Issues [#123](https://github.com/AlZrSe/sci-home-cluster/issues/123) (image + static serving), [#129](https://github.com/AlZrSe/sci-home-cluster/issues/129) (compose) - see [Docker Compose](#docker-compose-single-container-one-port) |

**Current Focus**: Phase 1 - Backend Foundation (FastAPI + SQLite + Syncthing sync) - **COMPLETE**

## Architecture Overview

This platform uses a client-server architecture with:

```
+----------------+     +----------------+     +----------------+
|                |     |                |     |                |
|  API Server    |<---->|  Syncthing     |<---->|  Worker Agents |
|  (FastAPI)     |     |  (File Sync)   |     |  (Python)      |
|                |     |                |     |                |
+----------------+     +----------------+     +----------------+
        ^                         ^
        |                         |
+----------------+     +----------------+
|                |     |                |
|    CLI/Web     |     |    Monitoring  |
|    Interface   |     |    Tools       |
|                |     |                |
+----------------+     +----------------+
```

- **API Server**: FastAPI + SQLite for job management and scheduling
- **Worker Agents**: Python scripts running on each node to execute jobs
- **Data Layer**: Syncthing for file sharing between nodes
- **Interface**: CLI and Web UI for job submission and monitoring
- **Workflow**: Agent-team skill with PM, SWE, QA, and On-Call Engineer roles

## Key Features

**Implemented (Phase 1):**
- Job submission via REST API (Web UI integrated)
- Job listing, filtering, pagination, metrics, logs
- Job retry/cancel workflow
- Exclusive node allocation (one job per node)
- GPU/CPU metrics collection and monitoring
- Manual retry workflow (user inspects logs before retry)
- Syncthing-based data sharing (single folder for input/output)
- Single shared token authentication + JWT tokens
- Localhost bypass for development
- SQLite database with Alembic migrations
- WebSocket log streaming

**Planned (Phase 2+):**
- CLI full API integration
- Windows and Linux support (currently Linux-focused)

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- Syncthing (for file synchronization)

### 1. Clone Repository
```bash
# Clone with submodules
git clone --recurse-submodules https://github.com/AlZrSe/sci-home-cluster.git
cd sci-home-cluster

# Or if already cloned:
git submodule update --init --recursive
```

### 2. Backend Setup
```bash
# Create virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies (pyproject.toml is at project root)
pip install -e .[dev]

# Run database migrations
cd backend && alembic upgrade head
```

> **Note**: Migrations use the same CWD-independent database resolver as the server.
> See [Which database am I using?](#which-database-am-i-using) for details.

### 3. Frontend Setup
```bash
cd frontend
npm install
```
The frontend is a git submodule at `frontend/` (repository: `AlZrSe/rendering-replicate`).

### 4. Syncthing Setup
1. Install Syncthing on all machines (including server)
2. Create a shared folder (e.g., `/syncthing-shared` or `D:\syncthing`)
3. Share the folder between all machines using Syncthing device IDs
4. Set the environment variable:
   ```bash
   # Linux/macOS
   export SYNCTHING_ROOT=/path/to/syncthing-shared
   # Windows PowerShell
   $env:SYNCTHING_ROOT="D:\syncthing-shared"
   ```

### 5. Running Tests
```bash
# Backend tests (with coverage) - run from backend directory
cd backend && pytest --cov=backend --cov-report=term-missing

# Frontend lint
cd frontend && npm run lint

# Or use make targets (Linux/macOS/WSL/Git Bash)
make test
make test-unit
make test-integration
```

---

## Running the Platform

### Quick Start (Two Terminals)

**Terminal 1 - Backend API Server:**
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

**Terminal 2 - Frontend Dev Server:**
```bash
cd frontend
npm run dev
```
- Frontend: http://localhost:5173 (or as shown by Vite)

> **Note**: The database file location is CWD-independent and anchored to the repository root.
> See [Which database am I using?](#which-database-am-i-using) for details on the canonical
> database path, how to override it, and cleaning up stale database files from before issue #34.

### Single Command (using concurrently)
```bash
# Install concurrently globally
npm install -g concurrently

# Run both from project root
concurrently "cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000" "cd frontend && npm run dev"
```

### Using Makefile (Linux/macOS/WSL/Git Bash)
```bash
# Show all available commands
make help

# Install all dependencies
make install-dev

# Run tests with coverage
make test

# Start backend dev server
make backend-dev

# Start frontend dev server
make frontend-dev

# Run database migrations
make db-upgrade

# Run all linting and type checks
make check
```

> **Note**: The Makefile currently provides `db-upgrade` and `db-downgrade` targets. Creating new migrations requires running `cd backend && alembic revision --autogenerate -m "message"` directly.

### Docker Compose (single container, one port)

The application is also a **single deployable unit**: one image that serves the SPA and the API on
one origin and one port, so there is no CORS to configure and nothing to reverse-proxy in
front of it. `docker-compose.yaml` sits at the repository root because the build context is the
root.

```bash
# 1. The frontend is a git submodule; a plain clone leaves it empty and the
#    build fails deliberately rather than shipping an image whose UI 404s.
git clone --recurse-submodules https://github.com/AlZrSe/sci-home-cluster.git
cd scientific-home-cluster

# 2. Start it. There is nothing to configure.
docker compose up -d
docker compose ps          # "(healthy)" comes from the image's own HEALTHCHECK
```

**No `.env` is required.** Both things that used to need one now have working
defaults:

| | Default | Override |
| --- | --- | --- |
| `SYNCTHING_ROOT` | `./syncthing` — a folder in this checkout, created and bind-mounted | put `SYNCTHING_ROOT` in a repository-root `.env` |
| `SHARED_TOKEN` | **not generated** - unset by default; `POST /api/v1/auth/token` returns `SHARED_TOKEN_NOT_CONFIGURED` until you set it (issue #64) | put `SHARED_TOKEN` in `.env` |

To read the generated token, and mint a JWT with it:

```bash
docker compose exec backend cat /app/.shc/shared_token
```

Then open <http://localhost:8000>. The SPA is served from `/` and every client-side route, the
API is at `/api/v1`, the docs at `/docs`.

**Prefer the token in `.env`?** `python docker/init_env.py` (or `make env-init`) seeds `.env`
from `.env.example`, writes a 64-character token, creates `syncthing/` and is idempotent —
`--show` prints the current one, `--rotate-token` replaces it. It needs Python on the host,
which is exactly why nothing requires it any more.

```bash
# 3. Start it
docker compose up -d
docker compose ps          # "(healthy)" comes from the image's own HEALTHCHECK
```

Then open <http://localhost:8000>. The SPA is served from `/` and every client-side route, the
API is at `/api/v1`, the docs at `/docs`.

#### Getting a token to sign in with

`SHARED_TOKEN` is not what you send to the API - it is exchanged for a short-lived JWT:

```bash
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"shared_token": "'"$(docker compose exec -T backend cat /app/.shc/shared_token)"'"}'
```

Opened as `http://localhost:8000`, the dashboard falls back to a localhost bypass and you may not
need this at all - see [`SHARED_TOKEN`](#environment-variables). Reaching it from another machine
(`http://192.168.x.x:8000`) is **outside** that bypass, so mint a real token there.

#### Things that bite

- **From another machine, the default API URL points at yourself.** `VITE_API_URL` is a
  *build-time* constant baked into the JS bundle and defaults to `http://localhost:8000/api/v1`.
  A browser on a different host will report the backend unreachable until you either rebuild with
  an externally reachable URL, or set it once on the app's settings page. It must stay absolute -
  the job-log WebSocket is derived with `base().replace(/^http/, "ws")`, and a relative base throws
  in a browser.
- **The published port is `8000:8000` on purpose**, and is not a variable, because it cannot be
  derived from `VITE_API_URL`. Changing one without the other is the same trap.
- **`SYNCTHING_ROOT` defaults to a real folder, not to nothing.** Unmounted, the backend would
  create an empty `/tmp/syncthing` itself, report the cluster available because the directory it
  just made exists, and no node would ever claim a submitted job. A project-local default you can
  see in `ls` is much harder to get wrong than the required absolute path it replaced
  ([#104](https://github.com/AlZrSe/sci-home-cluster/issues/104)).
- **`docker compose down -v` deletes both volumes.** `shc-data` holds the database and losing it
  loses the cluster; `shc-state` holds the JWT signing key and losing it invalidates every token
  ever issued. Plain `docker compose down` keeps both.
- **The worker agent is deliberately not a service here.** It runs on each compute node, needs that
  node's GPU and its own Syncthing folder, and `agent/README.md` does not exist yet
  ([#104](https://github.com/AlZrSe/sci-home-cluster/issues/104)). See
  [Worker Agent](#worker-agent-on-each-compute-node).
- `run-agent` *is* on `PATH` inside the image, so `docker compose exec backend run-agent` will
  actually run. Don't - it would run the one compute node inside the API container, behind the same
  SQLite volume, executing arbitrary job commands.

Full reference, including every environment variable the image understands:
[`docker/README.md`](docker/README.md).

### Deploy to Render.com

The project includes a **Render Blueprint** (`render.yaml` at the repository root) for
one-click deploys. Render is a managed PaaS; the container runs the API, frontend, and a
Syncthing daemon on a single public port.

**Prerequisites:**
- Render account with the repository connected (GitHub integration)
- A paid plan (**Starter** or above) — Render's free tier has no persistent disk, which is
  required for the SQLite database, JWT signing key, and Syncthing state

**Blueprint path (recommended):**
1. Commit `render.yaml` (already in this repo)
2. Render Dashboard → **New** → **Blueprint** → select this repository
3. Render imports the service, disk, and env vars from `render.yaml` and starts the build

**Manual path:**
1. Render Dashboard → **New** → **Web Service** → **Docker**
2. Dockerfile path: `docker/Dockerfile`, Docker context: `.` (repository root)
3. Health check path: `/api/v1/health`
4. Add a **Disk** mounted at `/app/data` (1 GB minimum)
5. Set environment variables (see `render.yaml`):
   - `SERVE_FRONTEND=true`
   - `SHC_DATA_DIR=/app/data`
   - `SHC_STATE_DIR=/app/data/syncthing` (JWT key lives here)
   - `SYNCTHING_ROOT=/tmp/syncthing` (synced folder; **not** the Syncthing home)
   - `SYNCTHING_HOME=/app/data/syncthing` (Syncthing identity, config, database — **on the disk**)
   - `DB_POOL=null` (the only value that boots on async SQLAlchemy)
   - `VITE_CLUSTER_BACKEND=http`
   - `VITE_API_URL=https://<your-service>.onrender.com/api/v1`
6. **Do not set `PORT`** — Render injects it automatically

**How it works:**
- The container binds to `${PORT:-8000}`; on Render `PORT` is injected (default 10000)
- A single Render Disk at `/app/data` holds everything: SQLite DB, JWT signing key,
  and Syncthing state (`config.xml`, `cert.pem`, `key.pem`, database)
- `VITE_API_URL` is a build-time constant; it must be the public HTTPS URL.
  If the service name changes, update `render.yaml` and redeploy, or retarget it once
  on the app's Settings page (runtime override without rebuild)
- The Syncthing daemon runs inside the container but its GUI (8384) and sync port (22000)
  are **not publicly reachable** on Render (single public port). No peers will register;
  jobs stay pending. This is expected for a Render deployment — see **Limitations** below.

**Retrieve the generated token:**
```bash
# In Render Shell
cat /app/data/syncthing/shared_token
```

**Limitations on Render:**
- No worker agents (Render cannot run GPU workloads)
- Syncthing runs locally but is not reachable for pairing (single public port)
- Single disk: DB, JWT key, and Syncthing state share `/app/data`; a disk failure loses all three
- `VITE_API_URL` is build-time; changing the public URL needs a rebuild (or Settings page override)
- Submodule dependency: if Render stops initializing submodules, the build fails

### CI/CD Pipeline

The project uses GitHub Actions for CI/CD with two workflows:

#### CI (`.github/workflows/ci.yml`)
Runs on every PR and push to `main`. Four independent jobs:
- `backend-gates` — ruff, mypy, pytest with >80% coverage
- `frontend-gates` — prettier, eslint, typecheck, vitest
- `compose-suite` — tests the checked-in `docker-compose.yaml` against a real Docker daemon
- `e2e` — Playwright tests against the built Docker image

All four are required status checks on `main`.

#### CD (`.github/workflows/cd.yml`)
Triggers automatically after CI passes on `main`:
1. **Build & Push** — Builds the Docker image and pushes to GHCR (`ghcr.io/<owner>/<repo>:<sha>`, `:latest`)
2. **Deploy Staging** — Triggers Render deploy to staging environment (auto-deploy)
3. **Deploy Production** — **Manual approval required** — triggers Render deploy to production

**Required GitHub Secrets for CD:**
| Secret | Description |
|--------|-------------|
| `RENDER_API_KEY` | Render API key (Account → Settings → API Keys) |
| `RENDER_SERVICE_ID_STAGING` | Render Service ID for staging (from service URL or API) |
| `RENDER_SERVICE_ID_PRODUCTION` | Render Service ID for production |

**To get a Render Service ID:**
```bash
# Via Render CLI
render services list

# Or from the service URL: https://dashboard.render.com/web/srv-<SERVICE_ID>
```

**GitHub Environments:**
- `production` environment must exist with required reviewers for manual approval

### Production Build
```bash
# Frontend production build
cd frontend && npm run build

# Backend - run with gunicorn (or similar)
cd backend && gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Worker Agent (on each compute node)

The agent is **file-only**: it never opens a network connection to the backend and holds no
credential. It reads the Syncthing folder, supervises its own tasks, and shuts down cleanly on
`SIGTERM`/`SIGINT`.

```bash
# Install once (editable is fine for development)
pip install -e .

# SYNCTHING_ROOT is required; there is no default, by design
export SYNCTHING_ROOT=/path/to/syncthing-shared

# Start the agent (run-agent is the installed console script)
run-agent --node-id node-01
```

Flags: `--node-id` (required, also readable from `$NODE_ID`), `--syncthing-root` (optional, wins over
`$SYNCTHING_ROOT`, expands `~` and relative paths), and `--log-level`.

**Configuration file.** The agent reads `<SYNCTHING_ROOT>/nodes/agent.toml`, so one edit configures
the whole fleet — the file replicates with the folder. Keys are flat: `AGENT_LOG_LEVEL`,
`AGENT_SHUTDOWN_GRACE_S`, `AGENT_FOLDER_WATCH_INTERVAL_S`, `AGENT_FOLDER_RETRY_MAX_S`,
`AGENT_WATCHER_LIVENESS_INTERVAL_S`, `AGENT_HEARTBEAT_INTERVAL_S`, `AGENT_HEARTBEAT_JITTER_S`,
`AGENT_STATE_DIR`, `AGENT_SHELL`. Unknown keys are rejected loudly.

Precedence is **CLI flag > environment variable > `nodes/agent.toml` > default**.

> **No hot reload.** `nodes/agent.toml` is read **once**, at startup. There is no `--config` flag and
> the location is derived from the resolved `SYNCTHING_ROOT`. Editing the file has no effect until
> you restart the agent.

`NODE_ID` and `SYNCTHING_ROOT` are deliberately **not** settable in `agent.toml`: every node reads
the same replicated file, so a `NODE_ID` there would make every agent claim the same identity, and
the root is what locates the file in the first place. Both are errors, not silent ignores.

**Heartbeat Contract** (issue #96): The agent writes a heartbeat every
`AGENT_HEARTBEAT_INTERVAL_S` seconds (default 22s, recommended `timeout/4`).
A uniform jitter of `±AGENT_HEARTBEAT_JITTER_S` (default ±2s) is applied to
prevent synchronized fleet wake-ups from overwhelming the watcher debounce.
The jitter is seeded from `(node_id, tick_count)` for deterministic test
behaviour. On startup and on resume from suspend (detected via monotonic
clock jump > `2 * interval`), the agent writes an immediate heartbeat.
The backend monitor checks every `HEARTBEAT_CHECK_INTERVAL_SECONDS` (default
30s) and marks a node OFFLINE if its heartbeat is older than
`HEARTBEAT_TIMEOUT_SECONDS` (default 90s). The invariant
`check_interval < timeout` is enforced at startup on both sides.

Every log line carries the node id (`[node=node-01]`), so four nodes writing into one journal stay
separable. Exit codes: `0` clean shutdown, `1` configuration error, `2` unexpected internal error,
`3` watcher liveness lost, `4` shutdown grace expired with tasks still running.

> **Which codes a stock process actually produces.** `1` (bad configuration) and `3` (a dead watcher)
> are reachable by themselves. `0` needs a `SIGTERM`/`SIGINT` the agent can catch: POSIX delivers
> one, Windows has no `add_signal_handler`, so there the agent only stops when the OS stops it and
> the exit code is the killer's rather than the agent's. `2` means a bug nobody planned for, and
> `4` means a task ignored the stop event — no shipped task does. Both are exercised by injecting
> the failure, not by running the agent.

### What shell runs a job

A submitted `command` is a **shell command line**, not a program. `AGENT_SHELL` chooses the shell
and defaults to `auto`:

| platform | `auto` resolves to |
|---|---|
| Windows | `cmd.exe /d /s /c` |
| Linux, macOS | `/bin/sh -c` |

`cmd.exe` and not PowerShell because a command written for a Windows box already means what the
operator expects: `%VAR%`, `&&`, `||`, `> file` and `cd /d`. PowerShell would silently redefine
all of them. Git for Windows' `sh.exe` is **not** the default either — it is only on `PATH` if
someone installed Git, and it is an MSYS build with different path, quoting and signal
semantics. It is selectable by name (`AGENT_SHELL = "bash"`) — "not the default" and "not
supported" are different claims.

Other accepted values: `sh`, `dash`, `bash`, `cmd`, `pwsh`, `powershell`, or a path whose name
is one of those. Anything else is a startup error listing every accepted value, because
`agent.toml` replicates and a typo has to fail on all nodes rather than be ignored on all nodes.
**Only the program is configurable.** The flags are fixed per shell (`/c`, `/d /s /c`,
`-NoProfile -NonInteractive -Command`) — a configurable flag set is how a quoting contract gets
broken.

> **Naming `bash` on Windows usually gets you WSL.** `sh`, `bash` and `dash` are resolved with
> `shutil.which`, and on a stock Windows install the only `bash.exe` on `PATH` is
> `C:\Windows\System32\bash.EXE` — the **WSL** launcher, not an argv `-c` shell. It will not
> understand `E:\…` paths, and its output is a second distribution's, not this node's. The agent
> will launch it, because that is what was asked for; the launch log line names the program it
> resolved, so `shell=C:\Windows\system32\bash.EXE` is the thing to look for. If you want a real
> POSIX shell on a Windows node, point `AGENT_SHELL` at Git's `sh.exe` by **path**:
> `AGENT_SHELL = "C:\\Program Files\\Git\\bin\\sh.exe"`.

> **The quoting contract.** The agent assembles `[shell program] + [fixed flags] + [command]` and
> hands it to the operating system **verbatim**. It performs no `shlex.split`, no re-quoting,
> no re-escaping and no environment substitution. The configured shell then parses the `command`
> exactly as it would if you had typed it at a prompt.
> **If it does not work when typed at a prompt, it does not work here.**

**On Windows the command line is assembled as one string, not as an argv list.** `CreateProcess`
takes a single command line, and Python composes it from a list with `list2cmdline`, which
implements the MSVCRT/`CommandLineToArgvW` quoting rules that `cmd.exe` does not use. Every
`command` that quotes anything would fail. Git-bash's `sh.exe` still gets the list form even on
Windows, because it is a real argv program — the distinction is the shell's parser, not the
platform.

**Stopping a job kills its whole tree**, on both platforms, in two tiers: a polite request, then
a forced one, then a **verification** that the tree is actually gone. On POSIX that is a process
group (`SIGTERM`, then `SIGKILL`). On Windows it is a process group *and* a Job Object armed with
`KILL_ON_JOB_CLOSE` (`CTRL_BREAK_EVENT`, then `TerminateJobObject` plus an independent
`taskkill /T /F` sweep). A cooperative stop is defeatable on both platforms, so the forced tier is
not optional. A job stopped by the agent is recorded `FAILED` with a reason; an operator who
wants it to run again must `POST /jobs/{id}/retry`.

**Job `error` codes** (the vocabulary #100 introduced; `exit_code` always carries the **real**
wait status, never a sentinel):

| `error` | `exit_code` | whose fault |
|---|---|---|
| `launch_failed:working_dir_not_found` | 1 | submitter |
| `launch_failed:working_dir_create_failed: …` | 1 | host |
| `launch_failed:input_missing` | 1 | submitter |
| `launch_failed:insufficient_gpus` | 1 | scheduling |
| `launch_failed:shell_not_found` | 127 | **the agent** — no shell on this machine, logged at ERROR with the PATH searched |
| `launch_failed:command_not_found` | 127 | submitter — only a POSIX shell reports 127, so this is POSIX-only; logged at INFO |
| `process_failed:exit_code=<N>` | `N` | submitter |
| `stopped:shutdown` | the real wait status | the agent |
| `stop_failed:tree_survived` | the real wait status | the agent — the forced stop ran and verification timed out |

These are **not** the agent's own exit codes below. A job's error vocabulary and an agent process
exit status are two different tables, and merging them is how a second one gets invented.

**`working_dir` resolution.** An absolute path on the host is used as-is — created if it is
inside the Syncthing root, required to exist otherwise. Anything else resolves against
`SYNCTHING_ROOT`, **never** against the current working directory.

**`resources.cpus` is advisory.** It is pinned with `sched_setaffinity` on Linux and logged at
INFO as advisory everywhere else; there is no portable pinning primitive. `memory_gb` and
`vram_gb` are not enforced at all. `gpus` **is** enforced — the claim is refused if the node
cannot fit.

### CLI Usage
```bash
# Submit a job (syncthing-root is required)
sci-run submit job.yaml --syncthing-root /path/to/syncthing

# List jobs
sci-run list

# View job logs (follow mode)
sci-run logs <job-id> --follow

# Cancel a job (NOT YET IMPLEMENTED)
# sci-run cancel <job-id>
```

> **Note**: The `sci-run cancel` command is a stub and not yet implemented.

> **Note**: the worker agent takes `--syncthing-root` as an *optional* override. It requires
> `SYNCTHING_ROOT` to be set by some means; see [Worker Agent](#worker-agent-on-each-compute-node).

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `SYNCTHING_ROOT` | Path to Syncthing shared folder | `/tmp/syncthing` |
| `DATABASE_URL` | SQLite database path. **Absolute** - see [Which database am I using?](#which-database-am-i-using) | `<repo-root>/data/scientific_home_cluster.db` |
| `SHC_STATE_DIR` | Where the generated JWT signing key is persisted (configurable via `.env` or environment) | `<repo-root>/.shc` |
| `DB_POOL` | Connection pool strategy: `pooled` or `null` | `pooled` |
| `SEED_DEMO_DATA` | Demo mode: seed the 10 demo jobs / 4 demo nodes on first use, **and** allow metrics and logs to be generated on read when nothing is stored | `false` |
| `SHARED_TOKEN` | Shared bearer token for API. **Not generated** - unset by default, so `POST /api/v1/auth/token` answers `SHARED_TOKEN_NOT_CONFIGURED` until you set it | none (unset) |
| `SECRET_KEY` | JWT signing key. See [Where is the signing key?](#where-is-the-signing-key) | generated into `$SHC_STATE_DIR` |
| `LOCALHOST_BYPASS` | Skip auth on localhost (legacy; prefer `LOCALHOST_BYPASS_MODE`) | `true` |
| `LOCALHOST_BYPASS_MODE` | Bypass mode: `disabled` (secure), `socket` (peer IP, default dev), `host_header` (legacy, insecure) | `socket` (if `LOCALHOST_BYPASS=true`) / `disabled` (if `LOCALHOST_BYPASS=false`) |
| `BACKEND_CORS_ORIGINS` | Allowed CORS origins | `["http://localhost:3000", "http://localhost:5173"]` |
| `OTEL_SERVICE_NAME` | OpenTelemetry service name | project name |
| `ENVIRONMENT` | Deployment environment | `development` |
| `ENABLE_API_DOCS` | Enable Swagger UI (`/docs`), ReDoc (`/redoc`), and OpenAPI schema (`/openapi.json`) in production. Disabled by default in production for security; enabled implicitly when `ENVIRONMENT=development` or `ENVIRONMENT=test`. | `false` |
| `APP_VERSION` | Deployed version | from `pyproject.toml` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint for traces/metrics | unset |
| `OTEL_ENABLED` | Enable OpenTelemetry instrumentation | `true` |
| `OTEL_SDK_DISABLED` | Disable SDK entirely | `false` |

#### Which database am I using?

There is exactly **one** canonical database file:

```
<repo-root>/data/scientific_home_cluster.db
```

The default is an **absolute** path derived from the *package location*, not
from your working directory, so it is the same file whether you run
`uvicorn backend.main:app` from the repo root, `uvicorn main:app` from
`backend/`, or `pytest`. The `data/` directory is created lazily on first
connect; importing the package creates nothing.

Previously the default was the relative literal
`sqlite:///./scientific_home_cluster.db`, which SQLite resolved against the
current working directory - so the server and the test suite used *different*
files and nothing in the logs or the API could say which ([issue #34]).

Three ways to find out, in order of convenience:

- **The startup log** names the absolute file:
  ```
  INFO:backend.core.database:Created async database engine: sqlite+aiosqlite:///.../data/scientific_home_cluster.db (database file: ...\data\scientific_home_cluster.db)
  ```
- **`GET /api/v1/health`** reports the absolute path as `database.url`.
- **The migrations** use the same resolver (`backend/alembic/env.py` calls
  `backend/core/database.py:resolve_database_url()`), so `alembic upgrade head`
  and the server can never migrate different files.

**Overriding it.** Set `DATABASE_URL` in the environment to point the cluster at
a database elsewhere, e.g. on your Syncthing volume. An **absolute** path is
strongly preferred - a relative one is resolved against your working directory,
so two launch directories mean two databases again. A relative value still
works (it is warned about at startup, never rejected), so nobody's working
configuration breaks.

**Precedence**, highest first:

1. the process environment (`DATABASE_URL=... uvicorn backend.main:app`)
2. `<CWD>/.env`
3. the default above

Note that step 2 is **CWD-relative**: pydantic-settings resolves `env_file=".env"`
against the current working directory, so launching from `backend/` ignores a
repo-root `.env`. That is deliberate for now - changing `.env` *discovery* would
break anyone relying on a CWD-local `.env`, so it is tracked separately from
this fix. Copy [`.env.example`](.env.example) to `.env` to get started.

> **Requires an editable install** (`pip install -e .`, as documented above). The
> default is anchored on the package location, which equals the checkout only for
> an editable install; from a wheel it would resolve inside the venv.

#### Stale database files from before #34

Four orphaned `*.db` files may be left over in older checkouts. All are
untracked, all are safe to delete, and none is read by the current code:

| Path | What it was |
|------|-------------|
| `./scientific_home_cluster.db` | the CWD-relative default, launched from the repo root |
| `backend/scientific_home_cluster.db` | the same default, launched from `backend/` (demo data) |
| `backend/tests/unit/scientific_home_cluster.db` | a 0-byte pre-#18 artifact of the old test setup |
| `e2e_test.db` | referenced by no source, test, target or document |

They were **not** migrated: the demo rows in `backend/scientific_home_cluster.db`
include a dangling `job-1039` foreign key, and the canonical database starts
empty and seeds on demand via `SEED_DEMO_DATA=true` (see [Demo Data](#demo-data)).

#### Where is the signing key?

```
<repo-root>/.shc/secret_key
```

It is **generated on first use**, never shipped. `SHC_STATE_DIR` overrides the
directory (used verbatim, relative or absolute); the default is absolute and
anchored on the package location, like the database path ([issue #34]).

The key signs every JWT. It is written `0600` on POSIX, and `.shc/` is in
`.gitignore` - at any depth - because a signing key has no business in a
repository, and two of them were in this one.

**Precedence**, highest first:

1. the process environment (`SECRET_KEY=... uvicorn backend.main:app`)
2. `<CWD>/.env`
3. the key already persisted in `$SHC_STATE_DIR`
4. a new key, generated and persisted

An explicit `SECRET_KEY` is **never** persisted and **never** rotated: it is not
written to `.shc/`, so it cannot be a leaked one, and an operator who set it
owns it. `GET /api/v1/health` deliberately does **not** report the key or its
path - `/health` has no auth dependency, and a credential's location is closer
to a credential than a database path is (issue #56).

Logging (mirrors the database path line from #34): one `INFO` naming the
absolute key path the first time a key is generated, one `WARNING` if a
published key was replaced, and **nothing at all** on an ordinary restart.

##### Upgrading from before #56

> **Two JWT signing keys were committed to this public repository**, at
> `.shc/secret_key` and `backend/.shc/secret_key`. The first is the one a
> default checkout was actually signing with, so **anyone who has cloned this
> repository could mint a token that the cluster accepts**, with an expiry of
> their choosing, for as long as that key stayed on disk.
>
> The files are no longer tracked ([issue #56]), but untracking does not
> unpublish: the blobs are still in git history, in every fork and every mirror.
> What closes the hole is **rotation**. `backend/core/config.py` carries a
> deny-list of the two keys' SHA-256 digests (digests, never key values - a
> digest cannot sign anything), and the first start after the upgrade replaces
> any key on the deny-list and logs a `WARNING`.
>
> **So the first start after upgrading mints a new key.** Every JWT issued
> before it stops verifying: `GET /api/v1/auth/verify` returns `{"valid": false}`
> and the frontend shows `AUTH_TOKEN_INVALID` / "Invalid or expired token".
> WebSocket log streams (issue #53) reject the old token too. This is intended -
> keeping the published key would keep the forge capability open.
>
> To get access back, in order of preference:
>
> 1. **Set `SHARED_TOKEN` in `.env` first**, restart, then
>    `POST /api/v1/auth/token {"shared_token": "..."}` mints a fresh JWT. This is
>    currently the only way to get a token over the API, because `SHARED_TOKEN`
>    is unset by default and is **not** auto-generated.
> 2. Delete `<repo-root>/.shc/secret_key` and start the backend, if you use the
>    localhost bypass and are happy to lose existing sessions.
> 3. Point `SHC_STATE_DIR` at an empty directory. `SHC_STATE_DIR` can be set in
>    `.env` or the environment (`SHC_STATE_DIR=/tmp/empty uvicorn backend.main:app`).

History is **not** rewritten ([issue #56], decision D1). Erasing the object does
not erase the copy, and a rotated key signs nothing - so rotation is the
control, not erasure.

### Demo Data

The backend seeds a demo dataset (10 jobs `job-1041`–`job-1050` and 4 nodes
`node-alpha`–`node-delta`, ported from the frontend mock server) into any
empty database on first use. **This is off by default.** A production
database starts empty and stays empty, which is what
[docs/api-server-core-spec.md](docs/api-server-core-spec.md) requires.

To get a populated dashboard locally:

```bash
SEED_DEMO_DATA=true uvicorn backend.main:app --reload
```

The flag is read at startup only. Flipping it at runtime would re-seed a
database that is meant to stay empty, which is the bug it exists to close.
The test suite sets it on for the whole session, since a large part of it
asserts on the demo dataset.

The backend logs one line at startup naming which mode it is in
(`SEED_DEMO_DATA=...`), because the two modes look identical from the
outside until you go looking for the difference.

> **Known behaviour:** with the seeder off, an empty cluster reports
> `GET /api/v1/health` as `status: "degraded"`, because store health is
> derived from the node count and no agent has registered yet. The endpoint
> still returns HTTP 200, so the frontend's reachability probe keeps
> working. Fixing the health contract is tracked in issue #32.

### Metrics and logs are never invented (issue #35)

With `SEED_DEMO_DATA=false` - the default - **reading** a metrics or logs
endpoint cannot write anything to the database. The store returns what an
agent actually reported, and nothing else. The contract in full:

| Resource | Flag | State | HTTP | Body |
|---|---|---|---|---|
| job that does not exist | either | – | **404** | `METRICS_NOT_FOUND` / `LOGS_NOT_FOUND` |
| node that does not exist | either | – | **404** | `NODE_METRICS_NOT_FOUND` |
| existing job | off | no samples | **200** | empty series (`[]` / `[]`, zero summary) |
| existing job | off | samples | **200** | the stored samples, read-only |
| existing node | off | no samples | **200** | empty series |
| log WebSocket | off | job `RUNNING` | – | closed with code `1008` and a reason; no line is sent or stored |
| any of the above | **on** | – | as on `main` | demo data is generated and persisted on read |

So: **404 means the resource does not exist; empty means it exists and
nothing has been collected yet.** A job you just created returns an empty
series rather than a 404, so the dashboard shows "no samples yet" instead
of an error.

> **The empty series has an all-zero `summary` (`gpu_util_avg: 0`,
> `gpu_memory_avg_mb: 0`).** That is the correct value for a series with
> no samples - but a client that reads only `summary` cannot tell "nothing
> collected yet" from "a GPU that measured nothing at all", because the
> two are genuinely the same data. **Detect an empty series with
> `gpu_metrics.length === 0 && cpu_metrics.length === 0`, never with the
> summary.** All seven summary fields are required integers, so an absent
> or null summary is not representable and `summary` must not be made
> optional.

`GET /jobs/{id}/logs` and `GET /jobs/{id}/logs/history` both answer
identically, and both 404 for a job id that does not exist.

#### Cleaning a database that was seeded already

There is deliberately **no automatic cleanup**. Seed rows live in the same
tables as real rows and are indistinguishable from them, so any heuristic
purge risks deleting a real cluster's data. To start clean, stop the
backend and delete the development database:

```bash
# Stop the backend first, then remove the database and its WAL sidecars.
# The path is the canonical <repo-root>/data/ one - the older
# backend/scientific_home_cluster.db* locations are stale demo data and are
# safe to delete too, but nothing reads them any more (issue #34).
rm -f data/scientific_home_cluster.db \
      data/scientific_home_cluster.db-shm \
      data/scientific_home_cluster.db-wal
```

For a database that also holds real jobs, delete only the known demo rows.

The demo metrics and logs are synthesised on read, so they have no rows in a
fresh database, but an older one may have persisted them. The foreign keys are
**circular** - `jobs.node_id` references `nodes.node_id` *and*
`nodes.current_job_id` references `jobs.job_id` - so no delete order alone
satisfies them. The cycle has to be broken first by clearing
`nodes.current_job_id`. The backend itself leaves `PRAGMA foreign_keys` off
(`backend/core/database.py:28`), so this only bites under tools that enforce
it - but the statement is written to be correct either way.

Note that this procedure's `job_id BETWEEN 'job-1041' AND 'job-1050'` clause
does **not** reach rows fabricated at read time for a real job id - see the
next section.

```bash
sqlite3 data/scientific_home_cluster.db <<'SQL'
PRAGMA foreign_keys=ON;
BEGIN;
-- Break the circular FK first: nodes.current_job_id -> jobs.job_id
UPDATE nodes SET current_job_id = NULL;
-- Children of jobs.job_id
DELETE FROM gpu_metrics WHERE job_id BETWEEN 'job-1041' AND 'job-1050';
DELETE FROM cpu_metrics WHERE job_id BETWEEN 'job-1041' AND 'job-1050';
DELETE FROM log_entries WHERE job_id BETWEEN 'job-1041' AND 'job-1050';
-- jobs.node_id -> nodes.node_id, so jobs go before nodes
DELETE FROM jobs WHERE job_id BETWEEN 'job-1041' AND 'job-1050';
DELETE FROM nodes WHERE node_id IN ('node-alpha','node-beta','node-gamma','node-delta');
COMMIT;
-- Must print nothing.
PRAGMA foreign_key_check;
SQL
```

Verify afterwards - all five should be `0` unless the database also held real
jobs:

```bash
sqlite3 data/scientific_home_cluster.db \
  "SELECT 'jobs',COUNT(*) FROM jobs
   UNION ALL SELECT 'nodes',COUNT(*) FROM nodes
   UNION ALL SELECT 'gpu_metrics',COUNT(*) FROM gpu_metrics
   UNION ALL SELECT 'cpu_metrics',COUNT(*) FROM cpu_metrics
   UNION ALL SELECT 'log_entries',COUNT(*) FROM log_entries;"
```

#### Cleaning rows that were fabricated at read time

Before issue #35 the store generated metrics and log lines **when a read
found nothing**, and then persisted them. Those rows are still in any
database written by an older backend, and they are worse than seeded demo
rows: they are attached to whatever job id happened to be read first,
including a **real, user-submitted job**, so they are indistinguishable
from real agent output. A `PENDING` job that never ran can show an hour of
60 % GPU utilisation.

There is deliberately **no automatic purge**, for the same reason as above:
fabricated rows live in the same tables as real rows, so any heuristic that
can find them can delete a real series.

The procedure is **detect (read-only) → review → delete by explicit id.**

*Step 1 - detect. Read-only; safe to run at any time.*

```sql
SELECT job_id
FROM gpu_metrics
GROUP BY job_id
HAVING COUNT(*) = 121                                   -- range(120, -1, -1), database_store.py
   AND COUNT(DISTINCT timestamp) = 121                   -- one sample per tick
   AND COUNT(DISTINCT gpu_index) = 1                     -- a real job pins its GPUs
   AND COUNT(DISTINCT memory_total_mb) = 1               -- always 24576 (hardcoded)
   AND ABS((julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 24 * 60 - 60) < 0.01
                                                          -- exactly a 60-minute span
ORDER BY job_id;
```

The same fingerprint for the log batch - 64 rows sharing **one** timestamp,
because the generator stamps the whole batch at once:

```sql
SELECT job_id FROM log_entries
GROUP BY job_id
HAVING COUNT(*) = 64 AND COUNT(DISTINCT timestamp) = 1
ORDER BY job_id;
```

> **This is a heuristic, not a proof.** A real agent sampling one GPU
> every 30 s for exactly one hour would match it. Read the job ids the
> query returns and check each one is a young or never-run job
> (`SELECT job_id, status, started_at FROM jobs WHERE job_id IN (...)`)
> **before** deleting anything.

*Step 2 - delete by explicit, reviewed id.*

```bash
sqlite3 data/scientific_home_cluster.db <<'SQL'
PRAGMA foreign_keys=ON;
BEGIN;
-- Replace <confirmed-id> with an id you have reviewed from step 1.
DELETE FROM gpu_metrics  WHERE job_id = '<confirmed-id>';
DELETE FROM cpu_metrics  WHERE job_id = '<confirmed-id>';
DELETE FROM log_entries WHERE job_id = '<confirmed-id>';
COMMIT;
-- Must print nothing.
PRAGMA foreign_key_check;
SQL
```

*Step 3 - verify.* Re-run both detection queries; they should now return
nothing.

If the database holds nothing you care about, the always-safe fallback is
still the one above: **stop the backend and delete the development
database file** (including the `-wal` and `-shm` sidecars).

A `source` / `synthetic` marker column would make this exact rather than
heuristic, and is tracked separately as issue #36. It cannot help rows that
already exist - those are permanently unattributable.

---

## Observability

The backend is instrumented with OpenTelemetry (traces + metrics). Service name,
deployment environment and deployed version are attached to every span and metric
via the OpenTelemetry `Resource`.

### Configuration

| Variable | Description |
|---|---|
| `OTEL_SERVICE_NAME` | Override the service name (default: project name) |
| `ENVIRONMENT` | Deployment environment (default: `development`) |
| `APP_VERSION` | Deployed version (default: version from `pyproject.toml`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint for traces and metrics |
| `OTEL_ENABLED` | Enable/disable instrumentation (default: `true`) |
| `OTEL_SDK_DISABLED` | Disable the SDK entirely (default: `false`) |

### Observability stack

A separate Compose project lives in `observability/`:

```bash
cd observability
cp .env.example .env   # fill in Grafana Cloud values
docker compose up -d
```

The application backend exports to the collector's published ports (4317 gRPC,
4318 HTTP). Point the backend at it with:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318
```

### Dashboard and alerts

- `observability/grafana-dashboard.json` — importable Grafana dashboard with
  the MVP metrics, filterable by `environment` and `deployed version`.
- `observability/alerts.yaml` — actionable alert for repeated canvas
  component-creation failures.

### On-call engineer

`on-call-engineer/poll_alerts.py` polls the observability alert API every
minute and passes firing alerts to a headless coding agent.

```bash
cd on-call-engineer
python poll_alerts.py
```

## Project Structure

```
scientific-home-cluster/
├── frontend/              # React Dashboard (Vite + Tailwind) ✅ COMPLETE
├── shared/                # Shared code (schemas, utilities)
├── backend/               # API Server (FastAPI + SQLite) — COMPLETE
├── agent/                 # Worker Node Agent (stub)
├── cli/                   # User CLI (Typer) (stub)
├── backend/tests/         # Unit & integration tests
├── docker/                # Dockerfile + container reference (image built from the root context)
├── docker-compose.yaml    # Single-container deployment (see Docker Compose above)
├── observability/         # Separate Compose project: OTel Collector + Grafana Cloud
├── on-call-engineer/      # Alert polling script for the on-call workflow
├── docs/                  # Architecture specs (API, tests, routers, etc.)
├── stubs/                 # Type stubs
├── openapi.yaml           # OpenAPI 3.0 specification
├── Makefile               # Development commands
├── pyproject.toml         # Python project config
├── PROCESS.md             # Development workflow documentation
└── README.md              # This file
```

---

## Development Workflow

This project uses the [agent-team skill](.opencode/skills/agent-team/SKILL.md) with specialized roles:

1. **Product Manager (PM)**: Creates specifications from requirements
2. **Software Engineer (SWE)**: Implements features and writes tests
3. **Tester/QA**: Verifies implementations against acceptance criteria
4. **On-Call Engineer**: Monitors CI/CD after code is merged

See [PROCESS.md](PROCESS.md) for detailed workflow instructions.