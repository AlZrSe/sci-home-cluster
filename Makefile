# Scientific Home Cluster - Development Makefile
# Provides convenient commands for backend and frontend development

.PHONY: help install install-dev test test-unit test-integration test-watch lint format typecheck clean
.PHONY: backend-dev backend-build backend-migrate frontend-dev frontend-build frontend-lint frontend-format
.PHONY: run-agent db-upgrade db-downgrade db-revision run
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
	@echo "  make test-watch       Run tests in watch mode"
	@echo "  make lint             Run ruff linter on backend"
	@echo "  make format           Format backend code with ruff"
	@echo "  make typecheck        Run mypy type checker on backend"
	@echo "  make backend-dev      Start backend API server (uvicorn with reload)"
	@echo "  make backend-migrate  Run database migrations (alembic upgrade head)"
	@echo "  make db-revision      Create new alembic migration revision"
	@echo "  make run-agent        Start worker agent"
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

# Backend setup
install-dev:
	cd backend && pip install -e .[dev]

install:
	cd backend && pip install -e .

# Testing
test:
	cd backend && pytest --cov=backend --cov-report=term-missing --cov-fail-under=80

test-unit:
	cd backend && pytest backend/tests/unit -v

test-integration:
	cd backend && pytest backend/tests/integration -v

test-watch:
	cd backend && pytest-watch --runner "pytest --cov=backend"

# Code quality
lint:
	cd backend && ruff check .

format:
	cd backend && ruff format .

typecheck:
	cd backend && mypy .

check: lint format typecheck

# Backend development
backend-dev:
	cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000

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
	cd agent && python run_agent.py --node-id node-01

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
	cd backend && rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	cd backend && find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	cd frontend && rm -rf dist .vite node_modules/.vite
	cd frontend && rm -rf coverage

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