"""
Database setup and session management for the Scientific Home Cluster backend.
Uses SQLAlchemy 2.0 async engine.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from sqlalchemy import event
from sqlalchemy.pool import NullPool, QueuePool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import absolute_sqlite_path, settings

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def resolve_database_url(database_url: Optional[str] = None) -> str:
    """
    The async SQLAlchemy URL for the database - the single source of truth.

    Both the application and Alembic resolve through this one function, so
    `alembic upgrade head` cannot migrate a different file than the server
    reads. It lives here, not in backend/alembic/env.py, because that module
    runs its migrations at import and so cannot be imported by a test (issue
    #34, implementation note I-1).

    Kept string-shaped on purpose: callers match the literal "sqlite://"
    prefix, and so does alembic.ini.
    """
    url = settings.DATABASE_URL if database_url is None else database_url
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def ensure_sqlite_parent_directory(
    database_url: Optional[str] = None,
) -> Optional[Path]:
    """
    Create the directory holding the SQLite file, immediately before connecting.

    Deliberately lazy and called from exactly two places - get_engine() and
    backend/alembic/env.py - because the canonical default lives in a directory
    that does not exist until something connects. Doing it here rather than
    while Settings is constructed is what keeps `import backend.core.config`
    free of filesystem side effects (issue #34, AC-4).

    Returns the absolute file path for non-file URLs too, so callers can log it.
    """
    url = settings.DATABASE_URL if database_url is None else database_url
    resolved = absolute_sqlite_path(url)
    if resolved is not None:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """
    Put SQLite into WAL mode with a real busy timeout.

    The Syncthing watcher runs on its own thread and writes to the same
    database as in-flight API requests. In the default rollback-journal
    mode a reader that later tries to write gets SQLITE_BUSY
    immediately (the classic deferred-transaction deadlock) and the
    request fails with "database is locked". WAL lets readers and the
    writer proceed, and busy_timeout makes competing writers wait
    instead of failing.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def get_engine() -> AsyncEngine:
    """Get or create the async database engine."""
    global _engine
    if _engine is None:
        # Convert sqlite:// to sqlite+aiosqlite:// for async
        db_url = resolve_database_url()
        # The canonical default points into <repo-root>/data/, which does not
        # exist until something connects (issue #34, AC-6).
        db_file = ensure_sqlite_parent_directory()

        _engine = create_async_engine(
            db_url,
            echo=settings.LOG_LEVEL == "DEBUG",
            pool_pre_ping=True,
            # Tests run many short-lived event loops against one SQLite file.
            # A pooled connection created in one loop and reused in the next
            # is what produces the intermittent "Event loop is closed"
            # failures, so the test session asks for NullPool.
            poolclass=NullPool if settings.DB_POOL == "null" else QueuePool,
        )
        if db_url.startswith("sqlite"):
            event.listen(_engine.sync_engine, "connect", _configure_sqlite)
        # Log the absolute location, not just the URL: the URL string alone
        # named a file without saying where it was, which is what made "which
        # database am I on?" unanswerable from the logs (issue #34, AC-8).
        logger.info(
            "Created async database engine: %s (database file: %s)",
            db_url,
            db_file if db_file is not None else "not a file URL",
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_database() -> None:
    """Initialize database connection and run migrations."""
    logger.info("Initializing database connection")
    # This will create the engine and session factory
    get_engine()
    get_session_factory()
    logger.info("Database connection initialized")


async def close_database() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connections closed")


async def run_migrations() -> None:
    """Run Alembic migrations to ensure database schema is up to date."""
    from alembic.config import Config
    from alembic import command
    from pathlib import Path

    try:
        alembic_ini_path = Path(__file__).parent.parent / "alembic.ini"
        alembic_cfg = Config(str(alembic_ini_path))
        # The URL is resolved from settings inside backend/alembic/env.py,
        # which is also what the `alembic` CLI uses, so both paths migrate
        # the same database.
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied successfully")
    except Exception as e:
        logger.error(f"Failed to apply database migrations: {e}")
        raise
