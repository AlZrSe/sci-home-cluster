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
The frontend is included in this repository at `frontend/`.

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

# Frontend tests (if configured)
cd frontend && npm test

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
| `DATABASE_URL` | SQLite database path | `sqlite:///./scientific_home_cluster.db` |
| `SHARED_TOKEN` | Shared bearer token for API | auto-generated |
| `SECRET_KEY` | JWT signing key | auto-generated |
| `LOCALHOST_BYPASS` | Skip auth on localhost | `true` |
| `BACKEND_CORS_ORIGINS` | Allowed CORS origins | `["http://localhost:3000", "http://localhost:5173"]` |

---

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

---

## Project Structure

```
sci-cluster/
├── frontend/              # React Dashboard (Vite + Tailwind) ✅ COMPLETE
├── shared/                # Shared code (schemas, utilities)
├── backend/               # API Server (FastAPI + SQLite)
├── agent/                 # Worker Node Agent
├── cli/                   # User CLI (Typer)
├── tests/                 # Test suite (top-level)
├── docker/                # Docker configurations
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

## License

MIT License - see [LICENSE](LICENSE) for details