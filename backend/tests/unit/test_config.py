"""
Unit tests for the configuration module.
"""

import logging
import os
import tempfile
from pathlib import Path

import pytest

from backend.core.config import (
    CANONICAL_DATABASE_PATH,
    DEFAULT_DATABASE_URL,
    DEFAULT_STATE_DIR,
    Settings,
    _state_dir,
    absolute_sqlite_path,
    sqlite_path_from_url,
)

# Anchored on this file, not the CWD, so these tests are themselves
# CWD-independent (the property they are asserting about).
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_settings_defaults():
    """Test that settings have correct default values."""
    # Create a temporary .env file for testing
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("SYNCTHING_ROOT=/tmp/test\n")
        env_file = f.name

    try:
        # Set env var BEFORE creating Settings (fixture checks os.environ first)
        os.environ["SYNCTHING_ROOT"] = "/tmp/test"
        # Hermeticity: a SEED_DEMO_DATA left in the developer's environment or
        # in a repo-root .env would otherwise decide this assertion.
        os.environ.pop("SEED_DEMO_DATA", None)
        settings = Settings(_env_file=env_file)

        assert settings.API_V1_STR == "/api/v1"
        assert settings.PROJECT_NAME == "Scientific Home Cluster API"
        assert settings.VERSION == "0.1.0"  # from pyproject.toml
        assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 1440  # 24 hours
        assert settings.SYNCTHING_ROOT == "/tmp/test"
        # Deliberately NOT the old relative literal "sqlite:///./scientific_home_cluster.db".
        # That assertion locked in the CWD-relative default this issue removed;
        # the default is pinned by test_database_url_default_is_absolute below.
        assert settings.DATABASE_URL == DEFAULT_DATABASE_URL
        assert settings.LOG_LEVEL == "INFO"
        assert settings.LOCALHOST_BYPASS
        # Demo seeding must be opt-in: a production database starts empty.
        assert settings.SEED_DEMO_DATA is False
    finally:
        # Clean up
        os.unlink(env_file)
        if "ENV_FILE" in os.environ:
            del os.environ["ENV_FILE"]
        if "SYNCTHING_ROOT" in os.environ:
            del os.environ["SYNCTHING_ROOT"]
        if "SEED_DEMO_DATA" in os.environ:
            del os.environ["SEED_DEMO_DATA"]


def test_cors_origins_parsing():
    """Test that CORS origins are parsed correctly from string."""
    # Test the validator logic directly with a string input
    from backend.core.config import Settings

    # Test that comma-separated string gets parsed to list
    cors_string = "http://localhost:3000,http://localhost:5173"
    result = Settings.assemble_cors_origins(cors_string)
    assert result == ["http://localhost:3000", "http://localhost:5173"]

    # Test that list input is returned as-is
    cors_list = ["http://localhost:3000", "http://localhost:5173"]
    result = Settings.assemble_cors_origins(cors_list)
    assert result == cors_list

    # Test default value
    settings = Settings()
    assert "http://localhost:3000" in settings.BACKEND_CORS_ORIGINS
    assert "http://localhost:5173" in settings.BACKEND_CORS_ORIGINS


# ---------------------------------------------------------------------------
# Issue #34: DATABASE_URL must not depend on the working directory.
# ---------------------------------------------------------------------------


def _build_settings() -> Settings:
    """
    A Settings instance that is hermetic and side-effect free.

    ``_env_file=None`` stops the CWD-relative ``.env`` discovery from deciding
    the result, and an explicit SECRET_KEY stops model_post_init from
    generating and persisting a signing key - which would itself write a file
    and invalidate the "this CWD stays empty" assertions below.
    """
    return Settings(_env_file=None, SECRET_KEY="test-key-not-persisted")


def test_database_url_default_is_absolute(tmp_path):
    """T1 / AC-1: the default points at an absolute path, not a relative one."""
    settings = _build_settings()

    url = settings.DATABASE_URL
    # The literal "./" is the shape of the old, CWD-relative default.
    assert "./" not in url
    assert url == DEFAULT_DATABASE_URL
    assert (
        url
        == "sqlite:///" + (REPO_ROOT / "data" / "scientific_home_cluster.db").as_posix()
    )
    assert CANONICAL_DATABASE_PATH == REPO_ROOT / "data" / "scientific_home_cluster.db"

    path = sqlite_path_from_url(url)
    assert path is not None
    assert path.is_absolute()
    assert path == CANONICAL_DATABASE_PATH
    # And the absolute form of it is itself absolute - the resolver agrees.
    assert absolute_sqlite_path(url) == CANONICAL_DATABASE_PATH


def test_database_url_independent_of_cwd(tmp_path, monkeypatch):
    """T2 / AC-2: two working directories resolve to one byte-identical URL."""
    first = tmp_path / "launched-from-here"
    second = tmp_path / "launched-from-there"
    first.mkdir()
    second.mkdir()

    urls = []
    for cwd in (first, second):
        monkeypatch.chdir(cwd)
        urls.append(_build_settings().DATABASE_URL)
        # Constructing Settings must not litter the directory it ran in.
        assert list(cwd.iterdir()) == []

    assert urls[0] == urls[1]
    assert sqlite_path_from_url(urls[0]).is_absolute()


def test_import_has_no_filesystem_side_effects(tmp_path):
    """
    T3 / AC-4: importing the application writes nothing into the CWD.

    This test MUST NOT set SECRET_KEY in the child env — doing so short-circuits
    _resolve_secret_key() and the test would pass even if DEFAULT_STATE_DIR
    reverted to the old relative ".shc" (which wrote ./.shc/secret_key into the
    launch directory). QA mutation-tested this: reverting DEFAULT_STATE_DIR to
    Path(".shc") and re-running ONLY this test still passed.

    Instead, we set SHC_STATE_DIR to a temp directory so key generation runs
    and is contained, then assert the launch directory stays empty.

    This test is one careless `setenv` away from proving nothing. Do not add
    SECRET_KEY to the child env.

    MUTATION TEST: To verify this test catches the DEFAULT_STATE_DIR regression,
    temporarily comment out the `env["SHC_STATE_DIR"] = str(key_dir)` line and
    revert DEFAULT_STATE_DIR to Path(".shc") in backend/core/config.py. The test
    should fail because the key will be generated in the launch directory.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parents[3]

    # Temp directory for the generated key (contained, not the launch dir)
    key_dir = tmp_path / "keys"
    key_dir.mkdir()

    env = dict(os.environ)
    # Filter out pytest-cov's COV_CORE_* variables to prevent coverage
    # measurement from leaking into the subprocess and corrupting the report.
    for key in list(env.keys()):
        if key.startswith("COV_CORE_"):
            del env[key]

    # The package is not installed on sys.path for the child, and the repo root
    # is the anchor under test, so hand it over explicitly.
    env["PYTHONPATH"] = str(REPO_ROOT)
    # Do NOT set SECRET_KEY — let _resolve_secret_key() generate one
    env["SHC_STATE_DIR"] = str(key_dir)
    env.pop("DATABASE_URL", None)
    env.pop("SECRET_KEY", None)  # Explicitly ensure it's not set

    launch_dir = tmp_path / "launch-from-here"
    launch_dir.mkdir()

    result = subprocess.run(
        [sys.executable, "-c", "import backend.main; import backend.core.config"],
        cwd=str(launch_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    # Launch directory must be empty — no .shc/ created there
    assert list(launch_dir.iterdir()) == [], (
        f"Launch directory {launch_dir} was not empty: {list(launch_dir.iterdir())}"
    )
    assert not (launch_dir / ".shc").exists(), ".shc created in launch directory"

    # Key should have been generated in the designated SHC_STATE_DIR
    assert (key_dir / "secret_key").exists(), "Key not generated in SHC_STATE_DIR"
    # File permission check (0o600) only meaningful on POSIX; Windows ignores chmod
    if sys.platform != "win32":
        assert (key_dir / "secret_key").stat().st_mode & 0o777 == 0o600, "Key not 0600"


def test_relative_database_url_warns(tmp_path, monkeypatch, caplog):
    """
    T4 / AC-9: an explicitly relative URL warns once, naming the resolution.

    Warned rather than rejected: someone pointing a relative DATABASE_URL at a
    database on their Syncthing volume must keep working, they just need to be
    told the file they get depends on the directory they launched from.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger="backend.core.config"):
        settings = Settings(
            _env_file=None, SECRET_KEY="k", DATABASE_URL="sqlite:///./x.db"
        )

    # Still usable - the value is passed through untouched.
    assert settings.DATABASE_URL == "sqlite:///./x.db"

    warnings = [r for r in caplog.records if "is relative" in r.getMessage()]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    # The warning has to name the absolute resolution, otherwise the developer
    # still cannot tell which file they opened.
    assert str(tmp_path / "x.db") in message
    # ...and point at the escape hatch.
    assert DEFAULT_DATABASE_URL in message


def test_explicit_database_url_env_var_wins(monkeypatch):
    """T5 / AC-9: the env var overrides the default and is not warned about."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/explicit-elsewhere.db")

    settings = _build_settings()
    assert settings.DATABASE_URL == "sqlite:////tmp/explicit-elsewhere.db"
    assert settings.DATABASE_URL != DEFAULT_DATABASE_URL


def test_state_dir_independent_of_cwd(tmp_path, monkeypatch):
    """T6 / AC-5: the state dir is absolute and SHC_STATE_DIR still wins."""
    monkeypatch.delenv("SHC_STATE_DIR", raising=False)

    first = tmp_path / "cwd-one"
    second = tmp_path / "cwd-two"
    first.mkdir()
    second.mkdir()

    monkeypatch.chdir(first)
    from_first = _state_dir()
    monkeypatch.chdir(second)
    from_second = _state_dir()

    assert from_first == from_second
    assert Path(from_first).is_absolute()
    assert from_first == str(DEFAULT_STATE_DIR)
    assert Path(from_first) == REPO_ROOT / ".shc"

    # An explicit override is used verbatim, relative or not: it is the user's
    # deliberate choice, so it is not made absolute behind their back.
    monkeypatch.setenv("SHC_STATE_DIR", "somewhere-relative")
    assert _state_dir() == "somewhere-relative"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("sqlite:///:memory:", None),
        ("sqlite://", None),
        ("postgresql://user:pw@localhost:5432/shc", None),
        ("sqlite:///./relative.db", Path("relative.db")),
        ("sqlite:////srv/shc/data.db", Path("/srv/shc/data.db")),
    ],
)
def test_sqlite_path_from_url(url, expected):
    """The URL parser handles every shape SQLAlchemy accepts."""
    assert sqlite_path_from_url(url) == expected
