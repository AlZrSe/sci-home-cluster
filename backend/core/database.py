"""
Database setup and session management for the Scientific Home Cluster backend.
Uses SQLAlchemy 2.0 async engine.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


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
        db_url = settings.DATABASE_URL
        if db_url.startswith("sqlite://"):
            db_url = db_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

        _engine = create_async_engine(
            db_url,
            echo=settings.LOG_LEVEL == "DEBUG",
            pool_pre_ping=True,
        )
        if db_url.startswith("sqlite"):
            event.listen(_engine.sync_engine, "connect", _configure_sqlite)
        logger.info(f"Created async database engine: {db_url}")
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
        # Override the sqlalchemy.url to use async driver
        db_url = settings.DATABASE_URL
        if db_url.startswith("sqlite://"):
            db_url = db_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied successfully")
    except Exception as e:
        logger.error(f"Failed to apply database migrations: {e}")
        raise
