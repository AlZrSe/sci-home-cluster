# Scientific Home Cluster — API Server Core Specification

## Overview

**Purpose**: Create the core FastAPI server with SQLite database, authentication, and essential REST/WebSocket endpoints for job and node management, with Alembic migration support.

**Scope**: This specification covers the creation of the main API server application that will:
- Serve as the central HTTP/WebSocket interface for the Scientific Home Cluster
- Manage persistence of job and node state via SQLite database with Alembic migrations
- Provide authentication via Bearer tokens (shared token + JWT)
- Implement core REST endpoints for job and node lifecycle management
- Provide WebSocket endpoints for real-time log streaming
- Integrate with the shared schemas and file operations modules
- Store persistent data in the Syncthing shared folder
- Run database migrations on startup

It does not cover:
- The Syncthing sync service itself (issue #4)
- The detailed worker agent implementation (covered in agent/README.md)
- Advanced features like job scheduling or priority queues (future enhancements)

## User Stories

- As a user, I want to submit jobs via API so that I can automate job submission
- As a user, I want to check job status via API so that I can monitor long-running computations
- As an administrator, I want to register and monitor worker nodes so that I can manage cluster resources
- As a developer, I want secure API access so that only authorized users can submit jobs
- As an operator, I want to view real-time job logs so that I can debug running computations
- As a user, I want to retry failed jobs so that I can recover from transient failures
- As a user, I want to cancel running jobs so that I can stop unwanted computations
- As a developer, I want database migrations to run automatically on startup so that schema changes are applied consistently

## Acceptance Criteria

### Core Application Setup
- [ ] Create `backend/main.py` as the application entry point
- [ ] Implement FastAPI app with lifespan events for startup/shutdown tasks
- [ ] Configure CORS middleware with configurable origins from settings
- [ ] Include API version prefix (`/api/v1`) for all endpoints
- [ ] Add request ID middleware for distributed tracing
- [ ] Configure OpenAPI/Swagger documentation at `/docs` and `/redoc`

### Database Layer with Alembic Migrations
- [ ] Implement SQLAlchemy 2.0 async models for persistence:
  - `JobDB` model matching `shared.schemas.job_state.JobState`
  - `NodeDB` model matching `shared.schemas.node_spec.NodeSpec`
  - Proper indexes on frequently queried fields (job_id, status, node_id)
- [ ] Create database session manager with async context handling
- [ ] Set up Alembic for database migrations:
  - Create initial migration for schema only (tables, indexes, constraints)
  - **NO seed data in migrations** - empty DB, Syncthing populates on startup
  - Migration files in `backend/migrations/versions/`
- [ ] Implement startup event that:
  - Initializes database connection
  - Runs Alembic migrations to latest version (`alembic upgrade head`)
  - Scans Syncthing folder for existing job/state files and populates database
- [ ] Implement shutdown event that closes database connections cleanly

### Authentication System
- [ ] Implement Bearer token authentication:
  - Auto-generate secure token on first run if not present in environment
  - Store token in file (`~/.config/sci-run/token`) or environment variable
  - Validate token on protected endpoints
  - Provide localhost bypass for development (similar to frontend)
- [ ] Create authentication dependencies:
  - `get_current_token` dependency for extracting token from headers
  - `require_auth` dependency for protecting endpoints
- [ ] Implement POST `/auth/validate` endpoint that returns `{ valid: boolean }`
- [ ] Implement POST `/auth/token` endpoint to exchange shared token for JWT
- [ ] Implement POST `/auth/refresh` endpoint to refresh JWT tokens

### REST Endpoints - Job Management
- [ ] POST `/jobs` - create job:
  - Accept multipart/form-data with `job.yaml` field containing YAML-encoded JobSpec
  - Validate job YAML against `shared.schemas.job_spec.JobSpec`
  - Generate job ID matching pattern `^job-\d+$`
  - Persist job to database and write initial state.yaml to Syncthing folder
  - Return 201 Created with job details
- [ ] GET `/jobs` - list jobs with filtering:
  - Support filtering by status, node_id, and text search (job name)
  - Support pagination with limit and offset parameters (default limit=10, max=100)
  - Return paginated list of job summaries
- [ ] GET `/jobs/{job_id}` - get job details:
  - Return complete job state including associated spec
  - Return 404 if job not found
- [ ] DELETE `/jobs/{job_id}` - delete job:
  - Remove job from database and Syncthing folder
  - Return 204 No Content on success
  - Return 404 if job not found
- [ ] POST `/jobs/{job_id}/retry` - retry failed job:
  - Only allowed for jobs in FAILED or CANCELLED status
  - Reset job to PENDING status, increment retry count
  - Clear previous error and completion timestamps
  - Return 200 OK with updated job state
  - Return 409 Conflict if job not in retryable state
- [ ] POST `/jobs/{job_id}/cancel` - cancel job:
  - Only allowed for jobs in PENDING or RUNNING status
  - Set job status to CANCELLED and update completion timestamp
  - Return 200 OK with updated job state
  - Return 409 Conflict if job not in cancellable state

### REST Endpoints - Node Management
- [ ] GET `/nodes` - list nodes:
  - Return list of all registered nodes with basic info
- [ ] GET `/nodes/{node_id}` - get node details:
  - Return complete node specification and current status
  - Return 404 if node not found
- [ ] POST `/nodes/register` - register/update node:
  - Accept NodeSpec model via JSON
  - Update last_heartbeat timestamp automatically
  - Create new node record or update existing one
  - Return 200 OK with node details

### Syncthing Management Endpoints
- [ ] GET `/syncthing/status` - get Syncthing service status:
  - Return connection status, device ID, folder status
- [ ] POST `/syncthing/scan` - trigger folder scan
- [ ] GET `/syncthing/connections` - list active connections

### WebSocket Endpoints
- [ ] GET `/jobs/{job_id}/logs/stream` - WebSocket endpoint for real-time logs:
  - Require authentication
  - Stream log lines as they become available
  - Send structured JSON messages: `{ "type": "log", "line": "log content", "timestamp": "..." }`
  - Send periodic heartbeat messages to maintain connection
  - Handle client disconnections gracefully
- [ ] GET `/jobs/{job_id}/metrics/stream` - WebSocket endpoint for real-time metrics:
  - Require authentication
  - Stream metrics samples as they become available
  - Send structured JSON messages: `{ "type": "metrics", "data": { ... }, "timestamp": "..." }`

### Error Handling & Custom Exception Handlers
- [ ] Custom exception handlers returning ErrorResponse format:
  - 400: Bad Request (invalid input)
  - 401: Unauthorized (missing/invalid token)
  - 403: Forbidden (authenticated but insufficient permissions)
  - 404: Not Found (resource doesn't exist)
  - 409: Conflict (resource state prevents operation)
  - 422: Unprocessable Entity (validation failed)
  - 500: Internal Server Error (unexpected failure)
- [ ] Standard error response format:
  ```json
  {
    "status": 404,
    "title": "Not Found",
    "detail": "Human-readable error message",
    "instance": "/api/v1/jobs/job-123"
  }
  ```

### Health Check Endpoint
- [ ] GET `/api/v1/health` - comprehensive health check:
  - Store status (healthy/degraded) with node/job counts
  - Config status (healthy/degraded) with syncthing_root, api_version
  - Syncthing status (not_configured/available/unhealthy) with path
  - Overall status and version from package metadata

### Supporting Functionality
- [ ] Input validation:
  - Validate job YAML against shared schemas
  - Validate node registration data
  - Validate path parameters and query strings
- [ ] Version from package metadata (read from pyproject.toml)
- [ ] OpenAPI/Swagger documentation:
  - Automatically generate docs at `/docs` and `/redoc`
  - Include accurate descriptions, parameters, and response examples
  - Mark authentication requirements clearly

## Technical Notes

### Application Architecture
- Use FastAPI with Uvicorn as the ASGI server
- Implement proper async/await throughout for database and file operations
- Structure code with separation of concerns:
  - `api/`: Route handlers and dependencies
  - `core/`: Application setup, configuration, security
  - `models/`: SQLAlchemy database models + Pydantic schemas
  - `services/`: Business logic layer
  - `store/`: Data persistence layer
  - `migrations/`: Alembic migration files

### Database Design
- Use SQLAlchemy 2.0 with async engine
- JobDB table should mirror JobState schema with appropriate field types
- NodeDB table should mirror NodeSpec schema
- Include created_at, updated_at timestamps for audit trail
- Use appropriate string lengths and constraints
- Proper indexes on job_id, status, node_id, created_at

### Alembic Migration Strategy
- Initial migration creates all tables, indexes, constraints
- **No seed data in migrations** - database starts empty
- Syncthing folder scan on startup populates database from YAML files
- Future schema changes use `alembic revision --autogenerate`
- Run migrations in lifespan startup: `await run_migrations()`
- Migration environment in `backend/migrations/env.py`

### Authentication Implementation
- Use Python-Jose (PyJWT) for JWT token handling
- Use HS256 algorithm for symmetric key encryption
- Token expiration: 24 hours (configurable via ACCESS_TOKEN_EXPIRE_MINUTES)
- Auto-token generation on first startup using secrets module
- Localhost bypass: Accept "localhost-no-auth" for localhost, 127.0.0.1, .local, .lovable.app
- Shared token stored in `SHARED_TOKEN` env var or config file

### File System Integration
- Use shared.file_ops module for all Syncthing folder interactions:
  - `normalize_path()` for path sanitization
  - `ensure_directory()` for directory creation
  - `get_jobs_directory()` and `get_nodes_directory()` for path resolution
  - `get_job_state_file()` and `get_node_state_file()` for specific file paths
  - `read_yaml()`, `write_yaml()` for atomic file operations
  - `file_lock()` and `shared_lock()` for concurrency control
- Store job state files as: `{SYNCTHING_ROOT}/jobs/{job_id}/state.yaml`
- Store node state files as: `{SYNCTHING_ROOT}/nodes/{node_id}.yaml`

### Performance Considerations
- Database connection pooling for concurrent requests
- Pagination limits to prevent excessive memory usage
- Streaming responses for large data sets when appropriate
- Efficient database queries with proper indexing
- Background tasks for non-critical operations (if needed)

## Dependencies

### Runtime Dependencies
- `fastapi`: ^0.100.0
- `uvicorn[standard]`: ^0.25.0
- `sqlalchemy[asyncio]`: ^2.0.0
- `python-multipart`: ^0.0.9 (for form handling)
- `python-jose[cryptography]`: ^3.3.0 (for JWT handling)
- `alembic`: ^1.13.0 (for database migrations)
- `pydantic-settings`: ^2.0.0 (for configuration)
- `psutil`: ^5.9.0 (for system metrics)
- `gputil`: ^1.4.0 (for GPU metrics)
- `watchdog`: ^4.0.0 (for file watching)
- `portalocker`: ^2.0.0 (for file locking)

### Development Dependencies
- `pytest`: ^8.0.0
- `pytest-asyncio`: ^0.23.0
- `httpx`: ^0.27.0
- `ruff`: ^0.5.0
- `mypy`: ^1.0.0
- `pytest-cov`: ^5.0.0

## Integration Considerations

### Shared Modules Integration
- Import and use `shared.schemas` models for data validation
- Use `shared.file_ops` for all Syncthing folder interactions
- Keep schema definitions in sync with frontend types

### Worker Agent Compatibility
- Ensure job state format matches what worker agents expect
- Use same field names and data types as defined in shared schemas
- Maintain backward compatibility where possible

## Directory Structure

After implementation, the backend module structure will be:

```
backend/
├── __init__.py
├── main.py                     # Application entry point
├── core/
│   ├── __init__.py
│   ├── config.py               # Application configuration (Pydantic Settings)
│   ├── security.py             # Authentication utilities (JWT, token validation)
│   ├── database.py             # Database setup and session management
│   └── deps.py                 # FastAPI dependencies
├── models/
│   ├── __init__.py
│   ├── job.py                  # SQLAlchemy JobDB model
│   ├── node.py                 # SQLAlchemy NodeDB model
│   ├── job_state.py            # Pydantic JobState model
│   ├── job_spec.py             # Pydantic JobSpec model
│   ├── job_list_result.py      # Pydantic JobListResult model
│   ├── job_metrics.py          # Pydantic JobMetrics model
│   ├── node_spec.py            # Pydantic NodeSpec model
│   ├── gpu_metric.py           # Pydantic GPUMetric model
│   ├── cpu_metric.py           # Pydantic CPUMetric model
│   ├── error_response.py       # Pydantic ErrorResponse model
│   └── token_validation.py     # Pydantic token validation models
├── services/
│   ├── __init__.py
│   ├── job_service.py          # Job business logic
│   ├── node_service.py         # Node business logic
│   ├── auth_service.py         # Authentication business logic
│   └── syncthing_service.py    # Syncthing integration
├── api/
│   ├── __init__.py
│   └── v1/
│       ├── __init__.py
│       ├── auth.py             # Authentication endpoints
│       ├── jobs.py             # Job management endpoints
│       ├── nodes.py            # Node management endpoints
│       ├── syncthing.py        # Syncthing management endpoints
│       └── metrics.py          # Metrics streaming endpoints
├── store/
│   ├── __init__.py
│   ├── memory.py               # In-memory store (for testing)
│   └── database.py             # Persistent database store
├── migrations/
│   ├── env.py                  # Alembic environment
│   ├── script.py.mako          # Migration template
│   └── versions/
│       └── 001_initial_schema.py  # Initial migration (schema only)
└── tests/
    ├── __init__.py
    ├── conftest.py             # Shared test fixtures
    ├── unit/
    │   ├── __init__.py
    │   ├── test_main.py
    │   ├── test_auth.py
│   │   ├── test_store.py
│   │   ├── test_jobs.py
│   │   ├── test_nodes.py
│   │   └── test_syncthing.py
    └── integration/
        ├── __init__.py
        ├── test_job_lifecycle.py
        ├── test_auth_flow.py
        └── test_node_management.py
```

## Implementation Notes

### Database Migration Strategy
- Initial migration creates tables, indexes, constraints only
- **No seed data in any migration** - database is empty after migration
- On startup after migrations: scan Syncthing folder, populate DB from YAML files
- Future schema changes: `alembic revision --autogenerate -m "description"`
- Apply migrations: `alembic upgrade head`

### Version Management
- Version read from `pyproject.toml` `[project] version = "x.y.z"`
- Exposed via `settings.VERSION` in config
- Used in FastAPI app creation, health endpoint, OpenAPI info

### Startup Sequence
1. Application startup begins
2. Load configuration (environment variables, config files)
3. Initialize database connection pool
4. Run Alembic migrations to latest version (`alembic upgrade head`)
5. Perform initial sync: scan Syncthing folder for existing job/state files
6. Populate database with any existing jobs/nodes found
7. Start Syncthing watcher service
8. Application ready to accept requests

### Shutdown Sequence
1. Application shutdown signal received
2. Stop accepting new connections
3. Wait for ongoing requests to complete (graceful shutdown)
4. Stop Syncthing watcher service
5. Close database connections
6. Clean up any temporary resources

## References

- [PROCESS.md](./PROCESS.md) - Development workflow
- [openapi.yaml](./openapi.yaml) - API contract that this implementation must satisfy
- [AGENTS.md](./AGENTS.md) - Agent instructions and project structure
- [shared-schemas-file-ops-spec.md](./shared-schemas-file-ops-spec.md) - Shared schemas specification
- [backend-setup-spec.md](./backend-setup-spec.md) - Backend project setup specification
- [in-memory-store-spec.md](./in-memory-store-spec.md) - In-memory store specification
- [003-api-server-core.md](./.github/ISSUE_TEMPLATE/003-api-server-core.md) - Original issue template
- [013-backend-main-app.md](./.github/ISSUE_TEMPLATE/013-backend-main-app.md) - Issue #15 template

---
*Specification for Issue #15 - Backend Main Application and Integration with Alembic Migrations*