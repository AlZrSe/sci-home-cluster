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
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.main import app
from backend.core.config import settings
from backend.core.database import close_database, get_session
from backend.core.security import create_access_token
from shared.schemas.job_spec import JobSpec
from shared.schemas.paths import Paths
from shared.schemas.resources import Resources
from shared.schemas.retry import RetryPolicy
from shared.schemas.node_spec import NodeSpec, GPUInfo
from backend.store import DatabaseStore, get_store


@pytest_asyncio.fixture(scope="session", autouse=True)
async def isolated_database():
    """
    Point the whole test session at a throwaway SQLite database.

    Without this the suite reads and writes the real
    ./scientific_home_cluster.db in the repo root, which makes tests
    non-hermetic and causes "database is locked" errors whenever the
    Syncthing watcher thread writes concurrently with a test.
    """
    from backend.store.database import Base

    tmpdir = tempfile.mkdtemp(prefix="shc-test-db-")
    db_path = Path(tmpdir) / "test.db"
    url = "sqlite:///" + db_path.as_posix()
    # Override the settings singleton only, not os.environ: the
    # DATABASE_URL default is asserted by test_config.py, and the engine
    # reads settings.DATABASE_URL anyway.
    settings.DATABASE_URL = url
    # Each test gets its own event loop, so a pooled connection created in
    # one loop and reused in another causes intermittent "Event loop is
    # closed" failures. Unpooled connections remove that class of bug.
    settings.DB_POOL = "null"
    # The demo dataset (10 jobs / 4 nodes) is off by default so a
    # production database starts empty. A large part of this suite asserts
    # on it, so the whole test session opts back in.
    #
    # Mutate the singleton rather than os.environ, for the same reason as
    # DATABASE_URL above: Settings is constructed at import time
    # (backend/core/config.py) and reads env/`.env` only at construction,
    # so setting the variable in the environment would be both too late
    # and leaky into tests that deliberately exercise the default.
    settings.SEED_DEMO_DATA = True

    # Create schema on the pytest-asyncio session loop
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + db_path.as_posix(), echo=False
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

    yield url
    shutil.rmtree(tmpdir, ignore_errors=True)


# Directories that cannot contain a database this suite is responsible for,
# and which are expensive to walk. `frontend/node_modules` and the virtualenvs
# in the repo root hold tens of thousands of files; an unpruned rglob over them
# made this fixture - which runs twice per session - the slowest thing in the
# suite by two orders of magnitude.
_WALK_PRUNED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)


def _is_pruned_dir(name: str) -> bool:
    """True for directories that must not be walked (see _WALK_PRUNED_DIRS)."""
    return name in _WALK_PRUNED_DIRS or name.startswith("venv")


@pytest.fixture(scope="session", autouse=True)
def assert_no_db_in_source_tree():
    """
        T12 / AC-10: the suite leaves no *.db behind anywhere under the repo.

        Isolated_database already points the session at a tempfile path; this makes
        the property permanent rather than accidental. It was a live defect once:
        a CWD-relative DATABASE_URL default made tests drop a database next to the
        test that constructed Settings (issue #34).
    The canonical <repo-root>/data/ directory is where a real server run
        legitimately writes, and a developer may well have one already. So it is
        not asserted empty - it is asserted *not to appear*. If data/ did not exist
        when the session started and exists when it ends, some test reached the real
        default DATABASE_URL instead of the temporary one, and that is a leak worth
        failing on.

        That exclusion used to be unconditional, which hid exactly this: the
        repository-wide intermittency in issue #57 sometimes leaves the engine
        pointed at the real default, and the resulting data/scientific_home_cluster.db
        was created and then waved through.

        Uses os.walk with in-place pruning rather than Path.rglob, because rglob
        cannot skip a subtree and would walk node_modules and every virtualenv.
    """
    repo_root = Path(__file__).resolve().parents[2]
    data_dir = (repo_root / "data").resolve()
    excluded = {
        (repo_root / "backend" / "tests" / "unit").resolve()
        / "scientific_home_cluster.db",
    }

    def _snapshot():
        found = set()
        for dirpath, dirnames, filenames in os.walk(repo_root):
            # Prune in place, so os.walk never descends into these.
            dirnames[:] = [d for d in dirnames if not _is_pruned_dir(d)]
            for filename in filenames:
                if not filename.endswith(".db"):
                    continue
                resolved = Path(dirpath, filename).resolve()
                if any(
                    resolved == skip or skip in resolved.parents for skip in excluded
                ):
                    continue
                found.add(resolved)
        return found

    before = _snapshot()
    data_existed = data_dir.exists()
    yield
    created = _snapshot() - before
    assert not created, (
        "The test suite created SQLite files in the source tree: "
        f"{sorted(str(p) for p in created)}. A test must be pointing the "
        "database at a temporary path - see the isolated_database fixture."
    )
    if not data_existed:
        assert not data_dir.exists(), (
            f"{data_dir} was created by this test run. A test reached the real "
            "default DATABASE_URL instead of the session temporary path, so it "
            "migrated and opened the canonical development database. See "
            "issue #57 for the intermittency that lets this happen."
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_database_engine():
    """
    Dispose the global async engine once the test session ends.

    aiosqlite runs a NON-daemon worker thread per connection, so an
    undisposed engine keeps the interpreter alive after pytest has
    finished and the process hangs forever. Disposing the engine closes
    those connections and lets the process exit.

    Runs on the pytest-asyncio session loop so the engine is disposed
    on the same loop it was created on.
    """
    yield
    await close_database()


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


@pytest_asyncio.fixture(autouse=True)
async def reset_singleton_store():
    """
    Reset the application store singleton before and after each test.

    Also stops any log stream the test left running. A WebSocket test opens
    a stream on the singleton store using the test client's event loop; if
    that task survives the test, the next test fails with "Event loop is
    closed" or blocks on the database write lock.

    Runs on the test's event loop so background tasks are cancelled and
    awaited on their native loop.
    """
    store = get_store()

    async def _reset_and_stop() -> None:
        await store.stop_all_log_streams()
        await store.reset()

    await _reset_and_stop()
    yield
    await _reset_and_stop()


# ============================================================================
# Demo-data seeding fixtures
# ============================================================================


@pytest_asyncio.fixture
async def seeding_disabled(monkeypatch) -> AsyncGenerator[DatabaseStore, None]:
    """
    Put the application store into production mode for a single test.

    Turns SEED_DEMO_DATA off on the settings singleton (not in os.environ,
    which Settings already consumed at import time), empties every table
    and clears the singleton's cached _seeded flag so the lazy seeder runs
    again - under the flag - on the next request.

    Emptied through store.reset() rather than by deleting rows directly,
    so the fixture also exercises the reset() gate. Deleting the rows
    behind its back would let a fix that gates only _ensure_seeded() pass
    these tests while the application singleton stayed populated.

    The flag is restored on teardown, and reset_singleton_store re-seeds
    afterwards, so the next test sees the normal seeded state.
    """
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    store = get_store()

    async def _clear() -> None:
        await store.reset()
        # reset() marks the store as seeded so it never re-seeds; undo that
        # so the very next request runs the gated lazy startup path, exactly
        # as a freshly started backend process would.
        store._seeded = False

    await _clear()          # Setup: runs on test's event loop
    try:
        yield store
    finally:
        await _clear()      # Teardown: runs on test's event loop


@pytest_asyncio.fixture
async def seeded_cluster(seeding_disabled) -> AsyncGenerator[DatabaseStore, None]:
    """
    A store whose demo dataset is explicitly present.

    /api/v1/health reports store "healthy" only when at least one node
    exists. That assertion used to pass solely because ambient seeding
    supplied 4 nodes; this fixture creates one explicitly so the health
    tests do not depend on the seeder being enabled.
    """
    store = seeding_disabled
    node = NodeSpec(
        node_id="health-node",
        hostname="health-node.lan",
        gpus=[GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)],
        cpus=8,
        memory_gb=32,
        os="Ubuntu 24.04",
        status="ONLINE",
        last_heartbeat=datetime.now(),
    )
    await store.create_node(node)
    yield store


# ---------------------------------------------------------------------------
# Per-test SEED_DEMO_DATA overrides
# ---------------------------------------------------------------------------
#
# These four started life in unit/test_seed_flag.py and were moved here so
# that every test which needs a specific flag state can ask for one
# instead of importing a fixture from another module (which ruff flags as
# a redefinition as soon as a test uses the name as a parameter).


@pytest.fixture
def flag_off(monkeypatch):
    """Force SEED_DEMO_DATA off for one test."""
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)


@pytest.fixture
def flag_on(monkeypatch):
    """Force SEED_DEMO_DATA on for one test (the test-session default)."""
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)


@pytest_asyncio.fixture
async def store_off(clean_database, flag_off) -> DatabaseStore:
    """A fresh DatabaseStore against empty tables, with seeding disabled."""
    return DatabaseStore()


@pytest_asyncio.fixture
async def store_on(clean_database, flag_on) -> DatabaseStore:
    """A fresh DatabaseStore against empty tables, with seeding enabled."""
    return DatabaseStore()


# ============================================================================
# Core Test Fixtures
# ============================================================================


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def clean_database() -> AsyncGenerator[None, None]:
    """
    Truncate every table around a single test.

    Isolation is achieved by clearing rows rather than by pointing the
    engine at a new file: rebuilding the engine per test leaks aiosqlite
    worker threads (they are non-daemon) which made the suite both very
    slow and prone to hanging at exit.
    """
    from backend.tests.conftest import _clear_store_data

    store = DatabaseStore()
    await _clear_store_data(store)
    try:
        yield
    finally:
        await _clear_store_data(store)


@pytest_asyncio.fixture
async def mock_store(clean_database) -> DatabaseStore:
    """
    An isolated, EMPTY store for unit tests.

    Empty means empty: the store is marked as already seeded so the lazy
    seeder does not repopulate it on first use.
    """
    store = DatabaseStore()
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


@pytest_asyncio.fixture
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
    # Absolute, so this class can never drop a test.db into whichever directory
    # the suite happens to be run from (issue #34, AC-11). It used to be the
    # relative literal "sqlite:///./test.db".
    DATABASE_URL: str = (
        "sqlite:///"
        + (Path(tempfile.gettempdir()) / "shc-test-settings" / "test.db").as_posix()
    )
    LOG_LEVEL: str = "DEBUG"
    BACKEND_CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:5173"]

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )
