# Scientific Home Cluster — Backend Tests Specification

## Overview

**Purpose**: Define comprehensive testing strategy, structure, and requirements for the backend API server to ensure correctness, prevent regressions, and maintain >80% code coverage.

**Scope**: This specification covers:
- Test directory structure and organization
- Shared fixtures in `conftest.py`
- Unit test patterns for all backend modules
- Integration test patterns for end-to-end API flows
- WebSocket testing patterns
- Coverage targets and reporting
- Test data generation strategies

## User Stories

- As a developer, I want a comprehensive test suite so that I can refactor with confidence
- As a developer, I want tests that validate API contract compliance so that I know the backend matches OpenAPI
- As a developer, I want tests that cover edge cases and error conditions so that the system is robust
- As a QA engineer, I want clear test organization so that I can run specific test suites
- As a CI/CD pipeline, I want fast, reliable tests that can run in parallel

## Acceptance Criteria

### Test Directory Structure
- [ ] Create `backend/tests/` directory with proper structure:
  ```
  backend/tests/
  ├── __init__.py
  ├── conftest.py              # Shared fixtures
  ├── unit/                    # Unit tests (fast, isolated)
  │   ├── __init__.py
  │   ├── test_main.py         # Main app tests
  │   ├── test_auth.py         # Authentication tests
  │   ├── test_config.py       # Configuration tests
  │   ├── test_store.py        # In-memory store tests
  │   ├── test_jobs.py         # Job service/router tests
  │   ├── test_nodes.py        # Node service/router tests
  │   ├── test_syncthing.py    # Syncthing service tests
  │   └── test_shared_*.py     # Shared module tests
  └── integration/             # Integration tests (slower, real components)
      ├── __init__.py
      ├── test_job_lifecycle.py
      ├── test_auth_flow.py
      ├── test_node_management.py
      └── test_websocket_streaming.py
  ```

### conftest.py Fixtures
- [ ] **`client`** - `httpx.AsyncClient` / `TestClient` for FastAPI app testing
  - Use `httpx.AsyncClient` with `ASGITransport` for better async support
  - Session-scoped fixture for performance
  - Automatically handles lifespan events
- [ ] **`auth_client`** - Pre-authenticated client with valid Bearer token
  - Creates token via `/auth/token` endpoint or directly via auth service
  - Adds Authorization header automatically
- [ ] **`sample_job`** - `JobSpec` fixture with valid test data
  - Minimal valid job spec for testing
  - Parameterizable for different test scenarios
- [ ] **`sample_node`** - `NodeSpec` fixture with valid test data
  - Valid node with GPU, CPU, memory configuration
  - Parameterizable for different node types
- [ ] **`mock_store`** - Isolated `InMemoryStore` instance for unit tests
  - Fresh instance per test (function-scoped)
  - Pre-populated with test data if needed
  - Reset/cleanup after each test
- [ ] **`db_session`** - Database session fixture for integration tests
  - Uses test database (SQLite in-memory or temp file)
  - Transaction rollback after each test
- [ ] **`syncthing_root`** - Temporary directory for Syncthing folder
  - Uses `tempfile.TemporaryDirectory`
  - Automatically cleaned up

### Test Framework Configuration
- [ ] Use `pytest` as primary testing framework
- [ ] Use `pytest-asyncio` for async test support (mode=auto)
- [ ] Use `httpx.AsyncClient` with `ASGITransport` for FastAPI testing
- [ ] Configure `pytest.ini` or `pyproject.toml` with:
  - `asyncio_mode = auto`
  - `testpaths = ["backend/tests"]`
  - `python_files = ["test_*.py"]`
  - `python_classes = ["Test*"]`
  - `python_functions = ["test_*"]`
  - Coverage configuration

### Coverage Requirements
- [ ] Target: **>80% coverage** for `backend` package
- [ ] Run with: `pytest --cov=backend --cov-report=term-missing`
- [ ] Exclude test files from coverage: `--cov-append --cov-ignore=backend/tests`
- [ ] Fail build if coverage drops below 80%: `--cov-fail-under=80`
- [ ] Generate HTML report for detailed analysis: `--cov-report=html`

### Test Data Generation
- [ ] Use **factory-boy** or similar for test data generation
  - `JobSpecFactory` for generating varied JobSpec instances
  - `NodeSpecFactory` for generating varied NodeSpec instances
  - `GPUInfoFactory` for GPU configurations
- [ ] Alternative: Pydantic model `model_copy(update={})` for variations
- [ ] Fixtures should support parameterization for different scenarios

### WebSocket Test Patterns
- [ ] Test WebSocket connection establishment:
  ```python
  async with client.websocket_connect("/api/v1/jobs/job-123/logs/stream") as ws:
      # Test connection
      # Test message receiving
      # Test disconnection
  ```
- [ ] Test structured message format:
  - Log messages: `{"type": "log", "line": "...", "timestamp": "..."}`
  - Status messages: `{"type": "status", "payload": "open|closed|connecting"}`
- [ ] Test heartbeat/ping-pong messages
- [ ] Test authentication on WebSocket (token in query param or header)
- [ ] Test graceful disconnection handling

### Test Patterns & Best Practices
- [ ] **Arrange-Act-Assert** pattern for all tests
- [ ] Tests must be **independent** and runnable in **any order**
- [ ] No shared mutable state between tests
- [ ] Use `asyncio` fixtures properly for async tests
- [ ] Mock external dependencies (file system, network, time)
- [ ] Test both **positive** (happy path) and **negative** (error) cases
- [ ] Test **boundary conditions** for all validations
- [ ] Use descriptive test names: `test_<feature>_<scenario>_<expected>`
- [ ] Group related tests in classes: `Test<Feature><Scenario>`

### Specific Test Coverage Areas

#### Authentication Module
- [ ] Token creation and validation (shared token + JWT)
- [ ] Password hashing and verification (if applicable)
- [ ] Localhost bypass functionality
- [ ] Dependency injection and error cases
- [ ] Token expiration and refresh
- [ ] Invalid/expired token handling

#### In-Memory Store
- [ ] All CRUD operations for jobs and nodes
- [ ] Filtering and pagination logic
- [ ] Metrics generation and caching
- [ ] Log generation and history
- [ ] Thread safety under concurrent access
- [ ] Reset functionality and seed data

#### Jobs Router/Service
- [ ] All endpoints with various input scenarios
- [ ] Authentication requirements (401 without token)
- [ ] Validation and error handling (422, 400)
- [ ] Job creation from YAML (multipart/form-data)
- [ ] Job retry and cancel logic (409 for invalid state transitions)
- [ ] Metrics and logs endpoints
- [ ] WebSocket log streaming

#### Nodes Router/Service
- [ ] Listing and detail endpoints
- [ ] Authentication requirements
- [ ] Error handling for missing nodes (404)
- [ ] Node registration/update

#### Main Application
- [ ] Startup and shutdown events
- [ ] Basic endpoints and health check
- [ ] CORS configuration
- [ ] OpenAPI schema validation
- [ ] Request ID middleware
- [ ] Exception handlers

#### Syncthing Service
- [ ] Status endpoint
- [ ] Folder scan trigger
- [ ] Connection listing

#### Integration Tests
- [ ] Full job lifecycle: create → list → get → cancel/retry → delete
- [ ] Database persistence across requests
- [ ] Syncthing file integration (YAML read/write)
- [ ] Authentication enforcement across all endpoints
- [ ] WebSocket streaming with real service

### Test Commands & CI Integration
- [ ] Add test script to `pyproject.toml`:
  ```toml
  [project.scripts]
  test = "pytest --cov=backend --cov-report=term-missing --cov-fail-under=80"
  test-unit = "pytest backend/tests/unit -v"
  test-integration = "pytest backend/tests/integration -v"
  test-watch = "pytest-watch --runner 'pytest --cov=backend'"
  ```
- [ ] Configure GitHub Actions / CI to run tests on PR
- [ ] Run unit tests in parallel for speed: `pytest -n auto`
- [ ] Separate integration test stage (may need real services)

## Technical Notes

### Async Testing with httpx
```python
# conftest.py
from httpx import AsyncClient, ASGITransport
from backend.main import app

@pytest.fixture(scope="session")
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

### Authentication in Tests
```python
# conftest.py
@pytest.fixture
async def auth_client(client, settings):
    # Get shared token or create one
    shared_token = settings.SHARED_TOKEN or "test-shared-token"
    response = await client.post("/api/v1/auth/token", json={"shared_token": shared_token})
    access_token = response.json()["access_token"]
    
    # Create authenticated client
    client.headers["Authorization"] = f"Bearer {access_token}"
    return client
```

### Factory Pattern for Test Data
```python
# tests/factories.py
import factory
from backend.models.job_spec import JobSpec, JobResources, JobPaths, JobRetry

class JobSpecFactory(factory.Factory):
    class Meta:
        model = JobSpec
    
    name = factory.Sequence(lambda n: f"test-job-{n}")
    command = "python train.py"
    working_dir = "/sync/projects/test"
    env = {}
    resources = factory.SubFactory(JobResourcesFactory)
    paths = factory.SubFactory(JobPathsFactory)
    retry = factory.SubFactory(JobRetryFactory)
```

### WebSocket Testing
```python
@pytest.mark.asyncio
async def test_websocket_log_streaming(auth_client):
    # Create a job first
    job_spec = JobSpecFactory()
    response = await auth_client.post("/api/v1/jobs", files={"job.yaml": job_spec.model_dump_yaml()})
    job_id = response.json()["job_id"]
    
    # Connect to WebSocket
    async with auth_client.websocket_connect(f"/api/v1/jobs/{job_id}/logs/stream") as ws:
        # Receive messages
        message = await ws.receive_json()
        assert message["type"] == "log"
        assert "timestamp" in message
```

### Database Testing
```python
# conftest.py - for integration tests using real database
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    
    await engine.dispose()
```

## Dependencies

### Test Dependencies (in pyproject.toml)
```toml
[project.optional-dependencies]
dev = [
    "pytest==8.0.0",
    "pytest-asyncio==0.23.0",
    "httpx==0.27.0",
    "pytest-cov==5.0.0",
    "factory-boy==3.3.0",  # For test data generation
    "pytest-xdist==3.5.0",  # For parallel test execution
    "pytest-watch==4.2.0",  # For watch mode
]
```

### Optional: Property-Based Testing
- Consider `hypothesis` for property-based testing of validation logic
- Useful for testing boundary conditions in JobSpec/NodeSpec validation

## Implementation Checklist

### Phase 1: Infrastructure
- [ ] Create `backend/tests/` directory structure
- [ ] Create comprehensive `conftest.py` with all required fixtures
- [ ] Add `factory-boy` factories for JobSpec, NodeSpec, GPUInfo
- [ ] Configure pytest in `pyproject.toml`
- [ ] Add test scripts to `pyproject.toml`

### Phase 2: Unit Tests
- [ ] Tests for main application (startup, health, CORS, exceptions)
- [ ] Tests for authentication (service + endpoints)
- [ ] Tests for in-memory store (CRUD, filtering, pagination)
- [ ] Tests for job service/router (all endpoints)
- [ ] Tests for node service/router (all endpoints)
- [ ] Tests for syncthing service/router
- [ ] Tests for configuration validation

### Phase 3: Integration Tests
- [ ] Job lifecycle integration test
- [ ] Authentication flow integration test
- [ ] Node management integration test
- [ ] WebSocket streaming integration test
- [ ] Database persistence test
- [ ] Syncthing file integration test

### Phase 4: Coverage & CI
- [ ] Run coverage and verify >80%
- [ ] Add coverage badge to README
- [ ] Configure CI pipeline
- [ ] Document test commands in README

## References

- [PROCESS.md](./PROCESS.md) - Development workflow and Definition of Done
- [api-server-core-spec.md](./api-server-core-spec.md) - API server specification
- [openapi.yaml](./openapi.yaml) - API contract
- [backend-setup-spec.md](./backend-setup-spec.md) - Backend project setup
- [in-memory-store-spec.md](./in-memory-store-spec.md) - In-memory store specification
- [014-backend-tests.md](./.github/ISSUE_TEMPLATE/014-backend-tests.md) - Issue #16 template

---
*Specification for Issue #16 - Backend Tests*