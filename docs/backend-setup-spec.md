# Scientific Home Cluster — Backend Project Setup Specification

## Overview

**Purpose**: Set up the backend project structure, dependencies, and configuration for the FastAPI server that will serve the Scientific Home Cluster API.

**Scope**: This specification covers the initial setup of the backend project, including directory structure, dependency management, configuration module, and basic project scaffolding. It does not cover the implementation of API endpoints, which will be addressed in subsequent issues.

## Tech Stack

- **Language**: Python 3.9+
- **Framework**: FastAPI
- **ASGI Server**: Uvicorn
- **Data Validation**: Pydantic v2
- **Authentication**: python-jose, passlib for JWT handling
- **File Uploads**: python-multipart
- **WebSockets**: websockets
- **YAML Handling**: pyyaml
- **Development Dependencies**: 
  - Testing: pytest, pytest-asyncio, httpx
  - Linting: ruff
  - Type Checking: mypy
- **Dependency Management**: pdm or pip with pyproject.toml (modern Python packaging)
- **Environment Variables**: Pydantic BaseSettings for configuration

## Architecture

The backend project will be structured as follows:

```
scientific-home-cluster/
├── backend/              # Backend project root (created per this specification)
│   ├── backend/          # Python package (named backend)
│   │   ├── __init__.py
│   │   ├── main.py       # FastAPI application entry point
│   │   ├── api/          # API route definitions (versioned)
│   │   │   ├── __init__.py
│   │   │   ├── v1/       # API version 1
│   │   │   │   ├── __init__.py
│   │   │   │   ├── jobs.py
│   │   │   │   ├── nodes.py
│   │   │   │   └── auth.py
│   │   ├── core/         # Core configuration, security, utilities
│   │   │   ├── __init__.py
│   │   │   ├── config.py # Pydantic settings
│   │   │   ├── security.py # Authentication utilities
│   │   │   └── utils.py
│   │   ├── models/       # Pydantic models (if not using schemas from shared)
│   │   │   └── __init__.py
│   │   └── services/     # Business logic layer
│   │       ├── __init__.py
│   │       ├── job_service.py
│   │       ├── node_service.py
│   │       └── auth_service.py
│   ├── tests/            # Backend-specific tests (unit and integration)
│   │   ├── __init__.py
│   │   ├── unit/
│   │   └── integration/
│   ├── pyproject.toml    # Project metadata and dependencies
│   ├── README.md         # Backend-specific README
│   └── .env.example      # Example environment file
├── frontend/             # React dashboard (Vite + TanStack + Tailwind) — COMPLETE
├── shared/               # Shared schemas, types, utilities (planned)
├── agent/                # Worker node agent (planned)
├── cli/                  # Typer CLI for job submission (planned)
├── tests/                # Root integration/e2e tests (planned)
├── docker/               # Docker configs (planned)
├── docs/spec.md          # Frontend specification
├── openapi.yaml          # API contract
├── PROCESS.md            # Development workflow
└── AGENTS.md             # This file
```

Note: The `tests/` directory at the project root is intended for end-to-end and integration tests that span multiple components (frontend, backend, agent, cli). The `backend/tests/` directory contains unit and integration tests specific to the backend package.

## Dependencies

### Runtime Dependencies
- `fastapi`: ^0.100.0
- `uvicorn[standard]`: ^0.25.0
- `pydantic`: ^2.0.0
- `python-jose[cryptography]`: ^3.3.0
- `passlib[bcrypt]`: ^1.7.4
- `python-multipart`: ^0.0.6
- `websockets`: ^12.0
- `pyyaml`: ^6.0

### Development Dependencies
- `pytest`: ^8.0.0
- `pytest-asyncio`: ^0.23.0
- `httpx`: ^0.27.0
- `ruff`: ^0.5.0
- `mypy`: ^1.0.0
- `pytest-cov`: ^5.0.0 (for coverage reporting)

## Configuration

The backend will use Pydantic v2 BaseSettings for configuration management, allowing settings to be overridden by environment variables.

Key configuration settings:
- `API_V1_STR`: "/api/v1"
- `PROJECT_NAME`: "Scientific Home Cluster API"
- `VERSION`: "1.0.0"
- `SECRET_KEY`: (auto-generated on first run if not set via environment)
- `ACCESS_TOKEN_EXPIRE_MINUTES`: 60 * 24 * 8 (8 days)
- `SYNCTHING_ROOT`: (path to Syncthing synchronization directory)
- `DATABASE_URL`: "sqlite:///./scientific_home_cluster.db" (for initial SQLite implementation)
- `BACKEND_CORS_ORIGINS`: list of allowed CORS origins (default: ["http://localhost:3000", "http://localhost:5173"] for frontend development)
- `LOG_LEVEL`: "INFO"

Localhost bypass logic: When running on localhost, 127.0.0.1, .local, or .lovable.app domains, the authentication requirement may be bypassed for development convenience (token auto-set to `"localhost-no-auth"`).

## Setup Instructions

### Prerequisites
- Python 3.9 or higher
- Git
- (Optional) PDM for dependency management (otherwise use pip)

### Installation Steps

1. Clone the repository:
   ```bash
   git clone https://github.com/AlZrSe/sci-home-cluster.git
   cd sci-home-cluster
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -e ./backend
   ```
   Or if using PDM:
   ```bash
   pdm install -C backend
   ```

4. Set up environment variables:
   ```bash
   cp backend/.env.example backend/.env
   # Edit backend/.env as needed
   ```

5. Initialize the database (if applicable):
   ```bash
   # For SQLite, tables will be created on first run
   ```

6. Run the development server:
   ```bash
   uvicorn backend.backend.main:app --reload
   ```

### Verification
To verify the installation works:
```bash
# Check that the server starts and responds to health endpoint
curl http://localhost:8000/api/v1/health
# Should return 200 OK with status information
```

## Testing Strategy

### Unit Tests
- Test configuration loading from environment variables
- Test Pydantic model validation
- Test security utilities (token generation/validation)
- Target: >80% coverage

### Integration Tests
- Test API endpoints with a test client
- Test database interactions (if applicable)
- Test WebSocket connections

### Setup Verification
- Verify that the `backend/` directory exists at project root
- Verify that the Python package is initialized with `pyproject.toml`
- Verify that required dependencies are installed
- Verify that development dependencies are installed
- Verify that basic package structure with `__init__.py` files is created
- Verify that a configuration module with Pydantic BaseSettings exists
- Verify that localhost bypass logic for development is implemented
- Verify that installation works in an isolated environment (e.g., fresh virtual environment)

## Acceptance Criteria (from Issue #8)

Given a developer setting up the backend project:
When they follow the setup instructions,
Then the following must be true:
- [ ] The `backend/` directory exists at project root
- [ ] Initialize Python package with `pyproject.toml` or `setup.py`
- [ ] Include required dependencies: fastapi, uvicorn, pydantic, python-jose, passlib, python-multipart, websockets, pyyaml
- [ ] Include dev dependencies: pytest, pytest-asyncio, httpx, ruff, mypy
- [ ] Create basic package structure with `__init__.py` files
- [ ] Add configuration module with Pydantic BaseSettings
- [ ] Implement localhost bypass logic for development
- [ ] Verify installation works in isolated environment

## Future Work

- Implementation of API endpoints (tracked in separate issues)
- Integration with Syncthing for file synchronization
- Implementation of the worker agent and CLI
- Database migrations (if moving beyond SQLite)
- Docker containerization
- CI/CD pipeline setup

## Open Questions

1. Should the Python package be named `backend` or `server`? The issue templates for backend components (007-014) assume a `backend/backend/` structure, implying the package is named `backend`. However, AGENTS.md references a `server/` directory. We have chosen to follow the issue templates to maintain consistency with existing backend-related issues.
2. How should we handle the generation of the secret key? Should we generate it on first run and store it in a file, or require it to be set via environment variable?
3. Should we include API documentation (Swagger/ReDoc) setup in this spec or leave it for the API server core issue?

## References

- [PROCESS.md](./PROCESS.md) - Development workflow
- [openapi.yaml](./openapi.yaml) - API contract that the backend must implement
- [AGENTS.md](./AGENTS.md) - Agent instructions and project structure
- Issue #8: [Backend Project Setup](.github/ISSUE_TEMPLATE/006-backend-project-setup.md)

---
*Specification ready for grooming. Move issue #8 to `groomed` once this document is created and reviewed.*