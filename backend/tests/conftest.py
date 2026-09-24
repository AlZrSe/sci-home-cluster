"""
Test configuration and fixtures for the backend test suite.
"""

import os
import tempfile
from datetime import datetime
from typing import AsyncGenerator, Generator, Optional

import pytest
from httpx import AsyncClient, ASGITransport
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.main import app
from backend.core.security import create_access_token
from backend.models.job_spec import JobSpec, Resources, Paths, RetryPolicy
from backend.models.node_spec import NodeSpec, GPUInfo
from backend.store.memory import InMemoryStore, get_store


@pytest.fixture(autouse=True)
def set_syncthing_root(monkeypatch):
    """
    Automatically set SYNCTHING_ROOT to a temporary directory for all tests.
    This ensures tests don't depend on external filesystem state.
    Only sets if not already set by the test (e.g., via _env_file).
    """
    # Only set if not already in environment (tests can override via .env file)
    if "SYNCTHING_ROOT" not in os.environ:
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("SYNCTHING_ROOT", tmpdir)
            yield
    else:
        yield


@pytest.fixture(autouse=True)
def reset_singleton_store():
    """
    Reset the in-memory store singleton before each test.
    This ensures test isolation for store-dependent tests.
    """
    # Import here to avoid circular imports

    store = get_store()
    # Use the existing reset method on the store instance
    import asyncio

    asyncio.run(store.reset())
    yield
    asyncio.run(store.reset())


# ============================================================================
# Core Test Fixtures
# ============================================================================


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Create an AsyncClient for testing the FastAPI app.
    Function-scoped to avoid event loop issues.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def auth_client(client: AsyncClient) -> AsyncClient:
    """
    Create an authenticated client with Bearer token.
    Uses the localhost bypass token "localhost-no-auth" for testing.
    """
    # Set the Authorization header with the localhost bypass token
    client.headers["Authorization"] = "Bearer localhost-no-auth"
    return client


@pytest.fixture
def sample_job() -> JobSpec:
    """Create a valid JobSpec factory fixture for testing."""
    return JobSpec(
        name="test-job",
        command="python train.py --epochs 10",
        working_dir="/sync/projects/test-job",
        env={"PYTHONUNBUFFERED": "1", "CUDA_VISIBLE_DEVICES": "0"},
        resources=Resources(gpus=1, cpus=4, memory_gb=16),
        paths=Paths(input="/sync/data/in", output="/sync/data/out"),
        retry=RetryPolicy(max_retries=3, retry_delay_seconds=60),
    )


@pytest.fixture
def sample_node() -> NodeSpec:
    """Create a valid NodeSpec factory fixture for testing."""
    return NodeSpec(
        node_id="test-node-01",
        hostname="test-node-01.lan",
        gpus=[GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)],
        cpus=16,
        memory_gb=64,
        os="Ubuntu 24.04",
        status="ONLINE",
        last_heartbeat=datetime.now(),
        current_job_id=None,
    )


@pytest.fixture
def mock_store() -> InMemoryStore:
    """
    Create an isolated InMemoryStore instance for unit tests.
    Fresh instance per test (function-scoped) with no seed data.
    """
    store = InMemoryStore()
    # Clear seed data - reset() re-seeds, so we need to clear manually
    import asyncio

    asyncio.run(_clear_store_data(store))
    return store


async def _clear_store_data(store: InMemoryStore):
    """Clear all data from the store without re-seeding."""
    async with (
        store._jobs_lock,
        store._nodes_lock,
        store._metrics_lock,
        store._logs_lock,
    ):
        store._jobs.clear()
        store._nodes.clear()
        store._metrics_cache.clear()
        store._log_history.clear()
        store._job_counter = 0
        for task in store._log_stream_tasks.values():
            task.cancel()
        store._log_stream_tasks.clear()
        store._log_stream_subscribers.clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Database session fixture for integration tests.
    Uses SQLite in-memory database with transaction rollback.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    # Create a minimal declarative base for testing
    class Base(DeclarativeBase):
        pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def syncthing_root() -> Generator[str, None, None]:
    """
    Temporary directory for Syncthing folder tests.
    Automatically cleaned up after test.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# ============================================================================
# Additional Helper Fixtures
# ============================================================================


@pytest.fixture
def test_settings():
    """Provide test settings with known values."""
    return TestSettings(
        SHARED_TOKEN="test-shared-token-123",
        SECRET_KEY="test-secret-key",
        LOCALHOST_BYPASS=True,
        SYNCTHING_ROOT="/tmp/test-syncthing",
    )


@pytest.fixture
def valid_jwt_token():
    """Generate a valid JWT token for testing."""
    return create_access_token({"sub": "test-user"})


@pytest.fixture
def expired_jwt_token():
    """Generate an expired JWT token for testing."""
    from datetime import timedelta

    return create_access_token(
        {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
    )


# ============================================================================
# Pytest Configuration
# ============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: mark test as a unit test")
    config.addinivalue_line("markers", "integration: mark test as an integration test")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line(
        "markers", "websocket: mark test as requiring WebSocket support"
    )


class TestSettings(BaseSettings):
    """Test settings that can be overridden."""

    SHARED_TOKEN: Optional[str] = "test-shared-token-123"
    SECRET_KEY: str = "test-secret-key-for-testing-only"
    LOCALHOST_BYPASS: bool = True
    SYNCTHING_ROOT: str = "/tmp/test-syncthing"
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Test API"
    VERSION: str = "1.0.0"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DATABASE_URL: str = "sqlite:///./test.db"
    LOG_LEVEL: str = "DEBUG"
    BACKEND_CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:5173"]

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )
