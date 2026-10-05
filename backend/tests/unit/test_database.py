"""
Unit tests for the database engine wiring.

Covers the shared URL resolver introduced in issue #34: the application and
Alembic must resolve the database through one function, the engine must create
its own parent directory lazily, and the startup log must name the absolute
file rather than a bare file name.
"""

import asyncio
import logging
from pathlib import Path

import pytest
from sqlalchemy.engine import URL

from backend.core import database as database_module
from backend.core.config import absolute_sqlite_path, settings
from backend.core.database import (
    ensure_sqlite_parent_directory,
    get_engine,
    resolve_database_url,
)


@pytest.fixture
def reset_engine_globals():
    """
    Reset the memoised engine between tests and restore it afterwards.

    get_engine() caches into module globals, so a test that points the engine
    at its own file would otherwise silently keep the engine the session already
    built, and pass without exercising anything.
    """
    saved_engine = database_module._engine
    saved_factory = database_module._session_factory

    database_module._engine = None
    database_module._session_factory = None
    yield

    engine = database_module._engine
    if engine is not None:
        # aiosqlite runs a NON-daemon worker thread per connection; leaving the
        # engine undisposed hangs the interpreter at exit.
        asyncio.run(engine.dispose())
    database_module._engine = saved_engine
    database_module._session_factory = saved_factory


def test_engine_creates_parent_directory(tmp_path, monkeypatch, reset_engine_globals):
    """
    T7 / AC-6: the SQLite parent directory is created on connect, not before.

    The canonical default lives in <repo-root>/data/, which does not exist in a
    fresh checkout, so the engine has to be able to create it. It must do so
    lazily: doing it while Settings is constructed would litter the filesystem
    on import (AC-4).
    """
    db_path = tmp_path / "nested" / "dir" / "x.db"
    assert not db_path.parent.exists()

    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///" + db_path.as_posix())

    engine = get_engine()

    # Constructing the engine is enough to prepare the directory...
    assert db_path.parent.is_dir()
    # ...and connecting is what actually creates the file.
    assert not db_path.exists()

    async def _connect() -> None:
        async with engine.connect():
            pass

    asyncio.run(_connect())
    assert db_path.is_file()


def test_engine_logs_absolute_path(tmp_path, monkeypatch, caplog, reset_engine_globals):
    """
    T8 / AC-8: the startup log names the absolute file.

    The old line logged the URL string, which for a relative default named a
    file without saying where it was - so "which database am I on?" could not be
    answered from the logs alone.
    """
    db_path = tmp_path / "logged" / "y.db"
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///" + db_path.as_posix())

    with caplog.at_level(logging.INFO, logger="backend.core.database"):
        get_engine()

    messages = [r for r in caplog.records if "database engine" in r.getMessage()]
    assert len(messages) == 1
    record = messages[0]
    message = record.getMessage()
    assert str(db_path) in message
    # The absolute path is logged as its own field, not only as part of the URL
    # string and not as a bare file name - a file name is identical for every
    # candidate database on disk, which is what made this unusable.
    logged_paths = [str(arg) for arg in record.args]
    assert str(db_path) in logged_paths
    assert any(Path(p).is_absolute() for p in logged_paths)


def test_resolve_database_url_is_shared_with_the_engine(
    tmp_path, monkeypatch, reset_engine_globals
):
    """
    AC-3: the engine opens the file the resolver names.

    resolve_database_url() is the single source of truth (issue #34, note I-1):
    backend/alembic/env.py calls it too, so if this drifts, `alembic upgrade
    head` and the server stop agreeing about which database exists.
    """
    db_path = tmp_path / "agreed" / "z.db"
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///" + db_path.as_posix())

    resolved = resolve_database_url()

    # sqlite:// -> sqlite+aiosqlite://, exactly as before this change.
    assert resolved == "sqlite+aiosqlite:///" + db_path.as_posix()
    assert absolute_sqlite_path(resolved) == db_path

    engine = get_engine()
    assert isinstance(engine.url, URL)
    assert engine.url.drivername == "sqlite+aiosqlite"
    assert Path(engine.url.database) == db_path


def test_resolve_database_url_honours_an_explicit_argument():
    """The resolver takes an override, so callers need not touch the singleton."""
    assert (
        resolve_database_url("sqlite:////tmp/elsewhere.db")
        == "sqlite+aiosqlite:////tmp/elsewhere.db"
    )
    # A non-SQLite URL is passed through untouched.
    assert (
        resolve_database_url("postgresql+asyncpg://user:pw@localhost/shc")
        == "postgresql+asyncpg://user:pw@localhost/shc"
    )


def test_ensure_sqlite_parent_directory_creates_nothing_for_memory():
    """A :memory: database has no parent directory to create."""
    assert ensure_sqlite_parent_directory("sqlite:///:memory:") is None


def test_ensure_sqlite_parent_directory_resolves_relative_urls(tmp_path, monkeypatch):
    """
    A relative URL still works - it is warned about, not rejected (AC-9).

    The directory created has to be the one SQLite will actually use, so it is
    resolved against the working directory rather than left relative.
    """
    monkeypatch.chdir(tmp_path)
    resolved = ensure_sqlite_parent_directory("sqlite:///./relative-dir/db.sqlite")

    assert resolved == tmp_path / "relative-dir" / "db.sqlite"
    assert (tmp_path / "relative-dir").is_dir()
