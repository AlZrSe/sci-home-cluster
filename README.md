# Scientific Home Cluster

A distributed platform for running scientific long-running applications on various computers connected to the internet.

## Project Status

| Component | Status | Description |
|-----------|--------|-------------|
| **Frontend Dashboard** | ✅ **Complete** | React + TypeScript + Vite + Tailwind (see `frontend/`) |
| Project Setup | ⏳ Ready | Issue [#1](https://github.com/AlZrSe/sci-home-cluster/issues/1) |
| Shared Schemas & File Ops | ⏳ Ready | Issue [#2](https://github.com/AlZrSe/sci-home-cluster/issues/2) |
| API Server Core | ⏳ Ready | Issue [#3](https://github.com/AlZrSe/sci-home-cluster/issues/3) |
| Syncthing Sync Service | ⏳ Ready | Issue [#4](https://github.com/AlZrSe/sci-home-cluster/issues/4) |
| Worker Agent | 📋 Planned | Phase 2 |
| CLI | 📋 Planned | Phase 2 |
| Docker/Production | 📋 Planned | Phase 3 |

**Current Focus**: Phase 1 - Backend Foundation (FastAPI + SQLite + Syncthing sync)

## Architecture Overview

This platform uses a client-server architecture with:
- **API Server**: FastAPI + SQLite for job management and scheduling
- **Worker Agents**: Python scripts running on each node to execute jobs
- **Data Layer**: Syncthing for file sharing between nodes
- **Interface**: CLI and Web UI for job submission and monitoring
- **Workflow**: Agent-team skill with PM, SWE, QA, and On-Call Engineer roles

## Key Features
- Job submission via CLI or Web UI
- Exclusive node allocation (one job per node)
- GPU metrics collection and monitoring
- Manual retry workflow (user inspects logs before retry)
- Syncthing-based data sharing (single folder for input/output)
- Single shared token authentication
- Windows and Linux support

## Getting Started

### 1. Create GitHub Repository
First, create the repository on GitHub:
1. Go to https://github.com/new
2. Repository name: `sci-home-cluster`
3. Description: Distributed scientific computing platform
4. Initialize with a README (optional)
5. Click "Create repository"

### 2. Set Up Local Repository
```bash
# Clone the repository
git clone https://github.com/AlZrSe/sci-home-cluster.git
cd sci-home-cluster

# Verify remote
git remote -v
# Should show origin pointing to GitHub
```

### 3. Development Setup
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Install pre-commit hooks (optional but recommended)
pre-commit install
```

### 4. Syncthing Setup
1. Install Syncthing on all machines (including server)
2. Create a shared folder (e.g., `/syncthing-shared` or `D:\syncthing`)
3. Share the folder between all machines using Syncthing device IDs
4. Set the environment variable: `export SYNCTHING_ROOT=/path/to/syncthing-shared`

### 5. Running the Platform
```bash
# Start the API server
uvicorn server.main:app --host 0.0.0.0 --port 8000

# Start an agent (on each worker node)
python agent/run_agent.py --node-id node-01 --syncthing-root /path/to/syncthing

# Use the CLI
sci-run submit job.yaml --syncthing-root /path/to/syncthing
sci-run list
sci-run logs <job-id> --follow
```

## Project Structure
```
sci-cluster/
├── frontend/              # React Dashboard (Vite + Tailwind) ✅ COMPLETE
├── shared/               # Shared code (schemas, utilities)
├── server/               # API Server (FastAPI + SQLite)
├── agent/                # Worker Node Agent
├── cli/                  # User CLI (Typer)
├── tests/                # Test suite
├── docker/               # Docker configurations
├── PROCESS.md            # Development workflow documentation
└── README.md             # This file
```

## Development Workflow

This project uses the [agent-team skill](.opencode/skills/agent-team/SKILL.md) with specialized roles:

1. **Product Manager (PM)**: Creates specifications from requirements
2. **Software Engineer (SWE)**: Implements features and writes tests
3. **Tester/QA**: Verifies implementations against acceptance criteria
4. **On-Call Engineer**: Monitors CI/CD after code is merged

See [PROCESS.md](PROCESS.md) for detailed workflow instructions.

## License

MIT License - see [LICENSE](LICENSE) for details