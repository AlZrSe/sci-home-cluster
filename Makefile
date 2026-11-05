# Scientific Home Cluster - Development Makefile
# Provides convenient commands for backend and frontend development

.PHONY: help install install-dev test test-unit test-integration test-compose test-watch lint format typecheck clean
.PHONY: backend-dev backend-migrate frontend-dev frontend-build frontend-build-dev frontend-lint frontend-format
.PHONY: check dev dev-full
.PHONY: run-agent db-upgrade db-downgrade db-revision run
.PHONY: env-init env-token
.PHONY: test-e2e test-e2e-ui test-e2e-headed test-all

# Default target
help:
	@echo "Scientific Home Cluster - Development Commands"
	@echo ""
	@echo "Backend:"
	@echo "  make install-dev      Install backend with dev dependencies"
	@echo "  make test             Run all tests with coverage"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-integration Run integration tests only"
	@echo "  make test-compose    Run the opt-in docker-compose suite (needs Docker;"
	@echo "                       creates and deletes shc-e2e-* volumes - see docker/README.md)"
	@echo "  make test-watch       Run tests in watch mode"
	@echo "  make lint             Run ruff linter on backend"
	@echo "  make format           Format backend code with ruff"
	@echo "  make typecheck        Run mypy type checker on backend"
	@echo "  make backend-dev      Start backend API server (uvicorn with reload)"
	@echo "  make backend-migrate  Run database migrations (alembic upgrade head)"
	@echo "  make db-revision      Create new alembic migration revision"
	@echo "  make run-agent        Start worker agent"
	@echo ""
	@echo "Docker Compose:"
	@echo "  make env-init         Seed .env (token + syncthing folder) for compose"
	@echo "  make env-token        Print the current SHARED_TOKEN"
	@echo ""
	@echo "Frontend:"
	@echo "  make frontend-dev     Start frontend dev server (vite)"
	@echo "  make frontend-build   Build frontend for production"
	@echo "  make frontend-build-dev Build frontend for development"
	@echo "  make frontend-lint    Run eslint on frontend"
	@echo "  make frontend-format  Format frontend with prettier"
	@echo "  make test-e2e         Run Playwright E2E tests"
	@echo "  make test-e2e-ui      Run Playwright E2E tests with UI"
	@echo "  make test-e2e-headed  Run Playwright E2E tests in headed mode"
	@echo ""
	@echo "Database:"
	@echo "  make db-upgrade       Run alembic upgrade head"
	@echo "  make db-downgrade     Run alembic downgrade -1"
	@echo "  make db-revision      Create new alembic revision (usage: make db-revision MSG='message')"
	@echo ""
	@echo "General:"
	@echo "  make clean            Remove build artifacts and cache"
	@echo "  make check            Run lint, format, and typecheck"
	@echo "  make test-all         Run all backend tests + frontend E2E tests"

# Backend setup (pyproject.toml lives at the repo root)
install-dev:
	pip install -e .[dev]

install:
	pip install -e .

# Testing
test:
	cd backend && pytest --cov=backend --cov-report=term-missing --cov-fail-under=80 -m "not compose"

test-unit:
	cd backend && pytest tests/unit -v

test-integration:
	cd backend && pytest tests/integration -v

# Opt-in suite that runs the checked-in docker-compose.yaml against a real Docker
# daemon (issue #134). Deliberately WITHOUT coverage: a live-container run
# contributes zero lines of backend/, so folding it into the coverage gate would
# only add minutes.
#
# It creates and deletes volumes named shc-e2e-* and republishes the hardcoded
# port 8000 on a free one; it NEVER touches shc-data or shc-state. See
# docker/README.md "Testing the compose file" before running it.
#
#   make test-compose
#   SHC_COMPOSE_TESTS=1 pytest tests/compose -m compose -k config   # from backend/: static layer, ~1s
#
# NB the path is `tests/compose`, not `backend/tests/compose`: the `cd backend`
# makes it relative to backend/, and pytest resolves a path argument against the
# invocation CWD, not against rootdir. test-unit and test-integration had the
# same bug until issue #136 - they named `backend/tests/...` from inside
# backend/, resolved to backend/backend/tests/..., and collected nothing while
# exiting 0. A documented gate that silently runs no tests is worse than one
# that is absent, so it is fixed here; #26 tracks the rest of this Makefile.
test-compose:
	cd backend && SHC_COMPOSE_TESTS=1 SHC_COMPOSE_ALLOW_DESTRUCTIVE=1 pytest tests/compose -m compose -v

test-watch:
	cd backend && pytest-watch --runner "pytest --cov=backend"

# Code quality
lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy .

check: lint format typecheck

# Backend development
backend-dev:
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

backend-migrate:
	cd backend && alembic upgrade head

db-upgrade:
	cd backend && alembic upgrade head

db-downgrade:
	cd backend && alembic downgrade -1

db-revision:
	@if [ -z "$(MSG)" ]; then echo "Usage: make db-revision MSG='description'"; exit 1; fi
	cd backend && alembic revision --autogenerate -m "$(MSG)"

run-agent:
	cd agent && python run_agent.py --node-id node-01 --syncthing-root $${SYNCTHING_ROOT:-../syncthing}

# Seed .env for `docker compose up`: copies .env.example, generates SHARED_TOKEN
# if absent, defaults SYNCTHING_ROOT to ./syncthing and creates it. Idempotent -
# an existing token is never rotated. See docker/init_env.py.
env-init:
	python docker/init_env.py

# Print the current SHARED_TOKEN without writing anything.
env-token:
	@python docker/init_env.py --show

# Frontend development
frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-build-dev:
	cd frontend && npm run build:dev

frontend-preview:
	cd frontend && npm run preview

frontend-lint:
	cd frontend && npm run lint

frontend-format:
	cd frontend && npm run format

# Frontend E2E tests
test-e2e:
	cd frontend && npm run test:e2e

test-e2e-ui:
	cd frontend && npm run test:e2e:ui

test-e2e-headed:
	cd frontend && npm run test:e2e:headed

# Test all
test-all: test test-e2e

# Cleanup
clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in (pathlib.Path('backend/__pycache__'), pathlib.Path('backend/.pytest_cache'), pathlib.Path('backend/.ruff_cache'), pathlib.Path('backend/.mypy_cache'), pathlib.Path('backend/dist'), pathlib.Path('backend/build'), pathlib.Path('frontend/dist'), pathlib.Path('frontend/.vite'), pathlib.Path('frontend/coverage'))]"
	python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"

# Development shortcuts
dev: backend-dev

run: dev-full

dev-full:
	@echo "Starting backend and frontend..."
	@echo "Backend will run on http://localhost:8000"
	@echo "Frontend will run on http://localhost:5173"
	@echo "Press Ctrl+C to stop both"
	@trap "kill 0" EXIT; \
	(cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000) & \
	(cd frontend && npm run dev) & \
	wait