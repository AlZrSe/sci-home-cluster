# Project Setup

## Description
Set up the initial project structure, dependencies, and development environment for the scientific home cluster platform.

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