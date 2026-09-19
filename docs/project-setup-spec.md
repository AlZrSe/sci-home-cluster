# Scientific Home Cluster — Project Setup Specification

## Overview

**Purpose**: Set up the initial project structure, dependencies, and development environment for the scientific home cluster platform.

**Scope**: This specification covers the initial repository setup including directory structure, dependency management, configuration files, and development environment instructions. It establishes the foundation for all subsequent development work.

## User Stories

- As a developer, I want a standardized project structure so that I can easily navigate and contribute to the codebase
- As a developer, I want clearly defined dependencies so that I can set up the development environment quickly
- As a developer, I want a README with clear instructions so that I can understand how to run and develop the platform

## Acceptance Criteria

- [ ] Project structure created: shared/, server/, agent/, cli/, web-ui/, tests/, docker/
- [ ] pyproject.toml includes all necessary dependencies (fastapi, uvicorn, sqlalchemy, pyyaml, pydantic, psutil, gputil, portalocker, typer, rich, watchdog)
- [ ] .gitignore excludes venv, __pycache__, *.log, .env, syncthing/ directory
- [ ] README.md includes architecture diagram, quickstart guide, and development setup instructions
- [ ] Development environment can be set up with: python -m venv venv && source venv/bin/activate && pip install -e .
- [ ] Initial commit pushed to main branch

## Technical Notes

- Use Python 3.9+ 
- Dependencies should be pinned to specific versions for reproducibility
- Consider using pre-commit hooks for code quality (ruff, mypy)
- The syncthing/ directory should be excluded from git as it's user-specific
- Note: The "server/" directory corresponds to the backend implementation
- Note: The "web-ui/" directory corresponds to the frontend implementation

## Directory Structure

After implementation, the project structure will be:

```
scientific-home-cluster/
├── shared/              # Shared schemas, types, utilities
├── server/              # Backend API server (FastAPI)
├── agent/               # Worker node agent
├── cli/                 # Typer CLI for job submission
├── web-ui/              # Frontend dashboard (React)
├── tests/               # Integration/e2e tests
├── docker/              # Docker configurations
├── docs/                # Documentation
├── .github/             # GitHub workflows and issue templates
├── .gitignore           # Git ignore rules
├── pyproject.toml       # Project dependencies and metadata
├── README.md            # Project overview and setup instructions
├── openapi.yaml         # API contract
├── PROCESS.md           # Development workflow
└── AGENTS.md            # Agent instructions
```

## Dependencies

### Runtime Dependencies
- `fastapi`: ^0.100.0
- `uvicorn[standard]`: ^0.25.0
- `sqlalchemy`: ^2.0.0
- `pyyaml`: ^6.0
- `pydantic`: ^2.0.0
- `psutil`: ^5.9.0
- `gputil`: ^1.4.0
- `portalocker`: ^2.0.0
- `typer`: ^0.9.0
- `rich`: ^13.0.0
- `watchdog`: ^4.0.0

### Development Dependencies
- `pytest`: ^8.0.0
- `pytest-asyncio`: ^0.23.0
- `httpx`: ^0.27.0
- `ruff`: ^0.5.0
- `mypy`: ^1.0.0
- `pytest-cov`: ^5.0.0

## Test Scenarios

### Directory Structure Tests
1. Verify that all required directories exist at project root:
   - shared/
   - server/
   - agent/
   - cli/
   - web-ui/
   - tests/
   - docker/

### Dependency Tests
2. Verify that pyproject.toml exists at project root
3. Verify that pyproject.toml contains all required runtime dependencies with correct version constraints
4. Verify that pyproject.toml contains all required development dependencies in [project.optional-dependencies] section

### Configuration Tests
5. Verify that .gitignore exists and contains patterns to exclude:
   - venv/
   - __pycache__/
   - *.log files
   - .env files
   - syncthing/ directory

### Documentation Tests
6. Verify that README.md exists at project root
7. Verify that README.md contains:
   - Architecture diagram or description
   - Quickstart guide
   - Development setup instructions matching: python -m venv venv && source venv/bin/activate && pip install -e .

### Environment Setup Tests
8. Verify that development environment can be set up successfully using the prescribed commands
9. Verify that after setup, the project can be imported/used without dependency errors

## Implementation Notes

- The backend work has already been completed in issue #8 (Backend Project Setup) which created the backend/server/ structure
- The frontend work is complete as indicated in AGENTS.md
- This specification focuses on creating the missing directories and root-level configuration files
- Existing work should be preserved and integrated where appropriate