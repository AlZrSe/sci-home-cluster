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
| Worker Agent | ✅ **Stub Implemented** | Phase 2 - basic structure, needs job execution logic |
| CLI | ✅ **Stub Implemented** | Phase 2 - commands scaffolded, needs API integration |
| Docker/Production | 📋 Planned | Phase 3 |

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
- Worker Agent job execution logic
- CLI full API integration
- Docker/Production deployment
- Windows and Linux support (currently Linux-focused)

---

## Getting Started

### Prerequisites
- Python 3.9+
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

### Production Build
```bash
# Frontend production build
cd frontend && npm run build

# Backend - run with gunicorn (or similar)
cd backend && gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Worker Agent (on each compute node)
```bash
# Set Syncthing root first (REQUIRED)
export SYNCTHING_ROOT=/path/to/syncthing-shared

# Start agent
cd agent
python run_agent.py --node-id node-01 --syncthing-root $SYNCTHING_ROOT
```

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

> **Note**: The `sci-run cancel` command is a stub and not yet implemented. The worker agent requires `--syncthing-root` as a required parameter.

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `SYNCTHING_ROOT` | Path to Syncthing shared folder | `/tmp/syncthing` |
| `DATABASE_URL` | SQLite database path. **Absolute** - see [Which database am I using?](#which-database-am-i-using) | `<repo-root>/data/scientific_home_cluster.db` |
| `SHC_STATE_DIR` | Where the generated JWT signing key is persisted | `<repo-root>/.shc` |
| `DB_POOL` | Connection pool strategy: `pooled` or `null` | `pooled` |
| `SEED_DEMO_DATA` | Demo mode: seed the 10 demo jobs / 4 demo nodes on first use, **and** allow metrics and logs to be generated on read when nothing is stored | `false` |
| `SHARED_TOKEN` | Shared bearer token for API. **Not generated** - unset by default, so `POST /api/v1/auth/token` answers `SHARED_TOKEN_NOT_CONFIGURED` until you set it | none (unset) |
| `SECRET_KEY` | JWT signing key. See [Where is the signing key?](#where-is-the-signing-key) | generated into `$SHC_STATE_DIR` |
| `LOCALHOST_BYPASS` | Skip auth on localhost | `true` |
| `BACKEND_CORS_ORIGINS` | Allowed CORS origins | `["http://localhost:3000", "http://localhost:5173"]` |

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
> 3. Point `SHC_STATE_DIR` at an empty directory. `SHC_STATE_DIR` is an
>    **environment** variable, so set it in the process environment
>    (`SHC_STATE_DIR=/tmp/empty uvicorn backend.main:app`). Putting it in `.env`
>    is rejected: it is not a `Settings` field and the extra-field check fails
>    every process at import ([issue #65]).

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

## Project Structure

```
scientific-home-cluster/
├── frontend/              # React Dashboard (Vite + Tailwind) ✅ COMPLETE
├── shared/                # Shared code (schemas, utilities)
├── backend/               # API Server (FastAPI + SQLite) — COMPLETE
├── agent/                 # Worker Node Agent (stub)
├── cli/                   # User CLI (Typer) (stub)
├── backend/tests/         # Unit & integration tests
├── docker/                # Docker configs (planned)
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