"""
Test configuration and fixtures for the backend test suite.
"""

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Generator, Optional

import pytest
from httpx import AsyncClient, ASGITransport
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.main import app
from backend.core.config import settings
from backend.core.database import close_database, get_engine, get_session
from backend.core.security import create_access_token
from backend.models.job_spec import JobSpec, Resources, Paths, RetryPolicy
from backend.models.node_spec import NodeSpec, GPUInfo
from backend.store import DatabaseStore, get_store


@pytest.fixture(scope="session", autouse=True)
def isolated_database():
    """
    Point the whole test session at a throwaway SQLite database.

    Without this the suite reads and writes the real
    ./scientific_home_cluster.db in the repo root, which makes tests
    non-hermetic and causes "database is locked" errors whenever the
    Syncthing watcher thread writes concurrently with a test.
    """
    import asyncio

    from backend.store.database import Base

    tmpdir = tempfile.mkdtemp(prefix="shc-test-db-")
    db_path = Path(tmpdir) / "test.db"
    url = "sqlite:///" + db_path.as_posix()
    # Override the settings singleton only, not os.environ: the
    # DATABASE_URL default is asserted by test_config.py, and the engine
    # reads settings.DATABASE_URL anyway.
    settings.DATABASE_URL = url

    async def _create_schema() -> None:
        # A throwaway engine: creating the schema through the global
        # engine would bind its pooled connections to this fixture's
        # event loop, which differs from the per-test loops.
        engine = create_async_engine(
            "sqlite+aiosqlite:///" + db_path.as_posix(), echo=False
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create_schema())

    yield url
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def dispose_database_engine():
    """
    Dispose the global async engine once the test session ends.

    aiosqlite runs a NON-daemon worker thread per connection, so an
    undisposed engine keeps the interpreter alive after pytest has
    finished and the process hangs forever. Disposing the engine closes
    those connections and lets the process exit.

    Deliberately synchronous: an async session-scoped fixture would need
    pytest-asyncio's function-scoped event_loop and raise ScopeMismatch.
    """
    yield
    import asyncio

    asyncio.run(close_database())


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

    # Run reset in the current event loop if available, otherwise create new
    try:
        loop = asyncio.get_running_loop()
        # Schedule the reset as a task
        asyncio.run_coroutine_threadsafe(store.reset(), loop).result(timeout=5)
    except RuntimeError:
        # No running loop, use asyncio.run
        asyncio.run(store.reset())
    yield
    try:
        loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(store.reset(), loop).result(timeout=5)
    except RuntimeError:
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
        transport=ASGITransport(app=app), base_url="http://testserver"
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
async def isolated_database_per_test() -> AsyncGenerator[str, None]:
    """
    Give a single test its own SQLite database file.

    Used by store unit tests that need full isolation from the seeded
    data the rest of the suite shares. The global engine is torn down and
    rebuilt because it caches the URL it was created with.
    """
    from backend.store.database import Base

    tmpdir = tempfile.mkdtemp(prefix="shc-store-")
    db_path = Path(tmpdir) / "store.db"
    url = "sqlite:///" + db_path.as_posix()
    previous_url = settings.DATABASE_URL

    settings.DATABASE_URL = url
    await close_database()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield url
    finally:
        await close_database()
        settings.DATABASE_URL = previous_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
async def mock_store(isolated_database_per_test) -> DatabaseStore:
    """
    An isolated, EMPTY store for unit tests.

    Empty means empty: the store is marked as already seeded so the lazy
    seeder does not repopulate it on first use.
    """
    store = DatabaseStore()
    await _clear_store_data(store)
    store._seeded = True
    return store


async def _clear_store_data(store: DatabaseStore) -> None:
    """Delete every row without re-seeding."""
    from backend.store.database import (
        CPUMetricModel,
        GPUMetricModel,
        JobModel,
        LogEntryModel,
        NodeModel,
    )

    async with get_session() as session:
        await session.execute(delete(LogEntryModel))
        await session.execute(delete(CPUMetricModel))
        await session.execute(delete(GPUMetricModel))
        await session.execute(delete(JobModel))
        await session.execute(delete(NodeModel))
        await session.commit()

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
