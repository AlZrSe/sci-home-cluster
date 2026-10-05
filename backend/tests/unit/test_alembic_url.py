"""
AC-3 / T9: the migration path and the application resolve the same database.

`backend/alembic/env.py` executes its migrations at module import, so it cannot
be imported by a test - `from alembic import context` fails outside an Alembic
run. That is precisely why issue #34 moved URL resolution out of env.py into
backend.core.database.resolve_database_url(); this test is what that structural
change buys, and it fails if the two paths ever diverge again.

The source of env.py is asserted textually rather than imported, so the two
copies of the rewrite rule cannot drift apart.
"""

import ast
from pathlib import Path

from backend.core.config import absolute_sqlite_path, settings
from backend.core.database import resolve_database_url

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_ENV = REPO_ROOT / "backend" / "alembic" / "env.py"
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"


def test_alembic_url_matches_app_url():
    """The shared resolver returns exactly what the application engine uses."""
    resolved = resolve_database_url()

    assert resolved.startswith("sqlite+aiosqlite:///")
    # It is the engine's file, and it is an absolute location - a relative one
    # would mean `alembic upgrade head` and the server could pick different files
    # depending on the directory each was launched from.
    assert absolute_sqlite_path(resolved) == absolute_sqlite_path(settings.DATABASE_URL)
    assert absolute_sqlite_path(resolved).is_absolute()


def test_alembic_env_delegates_to_the_shared_resolver():
    """env.py calls resolve_database_url() instead of rewriting the URL itself."""
    source = ALEMBIC_ENV.read_text(encoding="utf-8")
    tree = ast.parse(source)

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "resolve_database_url" in called, (
        "backend/alembic/env.py must resolve the URL through "
        "backend.core.database.resolve_database_url() so the migration path and "
        "the application cannot drift apart (issue #34, AC-3/I-1)."
    )
    # The parent-directory helper too, or `alembic upgrade head` against a fresh
    # checkout fails where the server would have succeeded (AC-6).
    assert "ensure_sqlite_parent_directory" in called


def test_alembic_ini_carries_no_competing_relative_default():
    """
    AC-7: alembic.ini holds no relative default that could be mistaken for ours.

    It used to read `sqlite+aiosqlite:///./scientific_home_cluster.db`, a third
    relative default - so `alembic upgrade head` could migrate a different file
    than the server reads. It is now a clearly-marked non-path fallback, because
    env.py always overrides it from Settings.
    """
    ini_text = ALEMBIC_INI.read_text(encoding="utf-8")
    url_lines = [
        line for line in ini_text.splitlines() if line.startswith("sqlalchemy.url")
    ]

    assert len(url_lines) == 1
    value = url_lines[0].split("=", 1)[1].strip()

    assert value != "sqlite+aiosqlite:///./scientific_home_cluster.db"
    # Whatever it says, it must not be a CWD-relative database file.
    assert "sqlite+aiosqlite:///./" not in value
    assert value == "sqlite+aiosqlite:///__set_by_settings__"


def test_alembic_creates_the_parent_directory(tmp_path, monkeypatch):
    """
    AC-6: the migration path creates data/ the same lazy way the engine does.

    Drives the same helper env.py calls, which is what makes the claim checkable
    without shelling out to the `alembic` CLI.
    """
    from backend.core.database import ensure_sqlite_parent_directory

    db_path = tmp_path / "data" / "scientific_home_cluster.db"
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///" + db_path.as_posix())

    assert not db_path.parent.exists()
    ensure_sqlite_parent_directory()
    assert db_path.parent.is_dir()
