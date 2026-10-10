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
import contextlib
import os
import sqlite3
import subprocess
import sys
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
    """
    env.py calls resolve_database_url() instead of rewriting the URL itself.

    THIS IS A SOURCE-TEXT CHECK ONLY, NOT BEHAVIOURAL EVIDENCE.

    It would pass even if env.py called resolve_database_url() and ignored the
    result. The behavioural test that pins the actual alembic CLI behaviour is
    test_alembic_upgrade_head_from_temp_cwd.
    """
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


def test_alembic_upgrade_head_from_temp_cwd(tmp_path):
    """
    AC-3/AC-7 behavioural: `alembic upgrade head` from any CWD migrates the
    canonical database and writes nothing to the launch directory.

    This is what QA validated by hand for #34 acceptance. The AST checks in
    test_alembic_env_delegates_to_the_shared_resolver are source-text guards
    only — they would pass if env.py called resolve_database_url() and ignored
    the result. This test pins the actual behaviour.
    """
    REPO_ROOT = Path(__file__).resolve().parents[3]
    ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"
    CANONICAL_DB = REPO_ROOT / "data" / "scientific_home_cluster.db"

    # Clean slate: remove any existing canonical database and WAL files
    for suffix in (".db", ".db-wal", ".db-shm"):
        (CANONICAL_DB.with_suffix(suffix)).unlink(missing_ok=True)

    # Whether data/ predates this test decides who has to clean it up. The
    # session fixture assert_no_db_in_source_tree fails if data/ did not exist
    # when the session started and exists when it ends, so a test that creates
    # it has to remove it again - and may only do so if it created it.
    data_dir_existed = CANONICAL_DB.parent.is_dir()

    # Capture existing .db files before running alembic (pre-existing from other tests)
    existing_dbs = set(REPO_ROOT.rglob("*.db"))

    # Ensure data/ directory exists (it's created lazily by the resolver)
    CANONICAL_DB.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    # Use a temp CWD that is NOT the repo root
    launch_dir = tmp_path / "launch-from-here"
    launch_dir.mkdir()

    # Run alembic upgrade head from the temp CWD
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=str(launch_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Alembic failed: {result.stderr}"

    # 1. Canonical database exists and is at head
    assert CANONICAL_DB.exists(), "Canonical database was not created"
    # Verify alembic_version table is at head
    #
    # `contextlib.closing`, NOT a bare `with sqlite3.connect(...)`: the sqlite3
    # connection context manager commits or rolls back and then returns, but it
    # does NOT close the connection. On Windows the file handle therefore stays
    # open for the rest of the function, the cleanup unlink below raises
    # PermissionError, the `except PermissionError: pass` swallows it, and
    # data/scientific_home_cluster.db survives the session - which then fails
    # assert_no_db_in_source_tree at teardown on any checkout where data/ did
    # not already exist. That was this test failing on `main` (issue #57).
    with contextlib.closing(sqlite3.connect(CANONICAL_DB)) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert version is not None, "alembic_version table missing"
    # Head revision as of #34 is '0003' (or whatever the latest is)
    # We just check it's not empty/missing; the exact value is in versions/
    assert version[0] != "", "alembic_version not set"

    # 2. Launch directory stayed empty (no relative default regression)
    assert (
        list(launch_dir.iterdir()) == []
    ), f"Launch directory {launch_dir} was not empty: {list(launch_dir.iterdir())}"

    # 3. No stray .db files anywhere under repo (except the canonical one)
    # Only check for NEW .db files created during this test run
    new_dbs = set(REPO_ROOT.rglob("*.db")) - existing_dbs
    stray_dbs = [p for p in new_dbs if p != CANONICAL_DB]
    assert stray_dbs == [], f"Stray .db files appeared: {stray_dbs}"

    # Cleanup for test isolation.
    #
    # The connection above is closed and the `alembic upgrade head` subprocess
    # has exited, so nothing holds the file and the unlink succeeds. The
    # `except PermissionError: pass` that used to be here is what hid the leak:
    # with the connection still open the unlink failed on every run, the error
    # was swallowed, and data/scientific_home_cluster.db outlived the session
    # (issue #57).
    for suffix in (".db", ".db-wal", ".db-shm"):
        CANONICAL_DB.with_suffix(suffix).unlink(missing_ok=True)

    # Leave the tree exactly as this test found it: data/ is created lazily by
    # the resolver above, and assert_no_db_in_source_tree treats a data/ that
    # appears during the session as a leak. Only remove it if we created it.
    if not data_dir_existed:
        CANONICAL_DB.parent.rmdir()
