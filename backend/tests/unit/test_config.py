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
    _resolve_secret_key,
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
    assert (
        list(launch_dir.iterdir()) == []
    ), f"Launch directory {launch_dir} was not empty: {list(launch_dir.iterdir())}"
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
    # Patch the global settings to ensure ambient SHC_STATE_DIR doesn't affect test.
    default_settings = Settings(_env_file=None, SECRET_KEY="test", SHC_STATE_DIR=None)
    monkeypatch.setattr("backend.core.config.settings", default_settings)

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
    # Create a new Settings instance with the override and patch the global.
    override_settings = Settings(
        _env_file=None, SECRET_KEY="test", SHC_STATE_DIR="somewhere-relative"
    )
    monkeypatch.setattr("backend.core.config.settings", override_settings)
    assert _state_dir() == "somewhere-relative"


# ---------------------------------------------------------------------------
# Issue #65: SHC_STATE_DIR as a Settings field.
# ---------------------------------------------------------------------------


def test_shc_state_dir_is_settings_field():
    """AC-7: Settings declares SHC_STATE_DIR as Optional[str] = None."""
    from backend.core.config import Settings

    # Check the field exists in model_fields and has the right default
    assert "SHC_STATE_DIR" in Settings.model_fields
    field = Settings.model_fields["SHC_STATE_DIR"]
    # Pydantic v2 stores Union[str, None] as the annotation for Optional[str]
    assert field.annotation is not None
    assert field.default is None


def test_shc_state_dir_in_env_file(tmp_path, monkeypatch):
    """AC-1, AC-2: SHC_STATE_DIR in .env is loaded and respected by _state_dir()."""
    # Clear ambient SHC_STATE_DIR so the .env is what gets tested.
    monkeypatch.delenv("SHC_STATE_DIR", raising=False)

    # Create a .env file with SHC_STATE_DIR
    env_file = tmp_path / ".env"
    custom_dir = tmp_path / "custom-shc"
    env_file.write_text(f"SHC_STATE_DIR={custom_dir}\n")

    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=str(env_file), SECRET_KEY="test")
    monkeypatch.setattr("backend.core.config.settings", settings)

    assert settings.SHC_STATE_DIR == str(custom_dir)
    assert _state_dir() == str(custom_dir)


def test_shc_state_dir_env_overrides_dotenv(tmp_path, monkeypatch):
    """AC-4: Process environment wins over .env (pydantic precedence)."""
    env_file = tmp_path / ".env"
    env_file.write_text("SHC_STATE_DIR=/from-dotenv\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHC_STATE_DIR", "/from-env")

    settings = Settings(_env_file=str(env_file), SECRET_KEY="test")
    monkeypatch.setattr("backend.core.config.settings", settings)

    assert settings.SHC_STATE_DIR == "/from-env"
    assert _state_dir() == "/from-env"


def test_shc_state_dir_relative_path_preserved(tmp_path, monkeypatch):
    """AC-5: Relative paths in SHC_STATE_DIR are used verbatim."""
    monkeypatch.delenv("SHC_STATE_DIR", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("SHC_STATE_DIR=./relative-shc\n")

    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=str(env_file), SECRET_KEY="test")
    monkeypatch.setattr("backend.core.config.settings", settings)

    assert settings.SHC_STATE_DIR == "./relative-shc"
    assert _state_dir() == "./relative-shc"


def test_shc_state_dir_key_persistence(tmp_path, monkeypatch):
    """AC-6: JWT signing key is written to the configured SHC_STATE_DIR."""
    monkeypatch.delenv("SHC_STATE_DIR", raising=False)

    # Clean up any existing key
    key_dir = tmp_path / "test-shc-key-dir"
    if key_dir.exists():
        import shutil

        shutil.rmtree(key_dir)

    env_file = tmp_path / ".env"
    env_file.write_text(f"SHC_STATE_DIR={key_dir}\n")

    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=str(env_file), SECRET_KEY="")
    monkeypatch.setattr("backend.core.config.settings", settings)

    # Access SECRET_KEY to trigger _resolve_secret_key() via model_post_init
    key = settings.SECRET_KEY

    assert len(key) == 43  # token_urlsafe(32) length
    assert (key_dir / "secret_key").exists()
    assert (key_dir / "secret_key").read_text() == key
    # Check permissions on POSIX
    import sys

    if sys.platform != "win32":
        assert (key_dir / "secret_key").stat().st_mode & 0o777 == 0o600


def test_settings_singleton_same_shc_state_dir():
    """T6: Multiple imports share same settings instance with same SHC_STATE_DIR."""
    from backend.core.config import settings as settings1
    from backend.core.config import settings as settings2

    assert settings1 is settings2
    assert settings1.SHC_STATE_DIR == settings2.SHC_STATE_DIR


def test_secret_key_env_bypasses_state_dir(tmp_path, monkeypatch):
    """T7: SECRET_KEY env var bypasses state dir entirely."""
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()

    env_file = tmp_path / ".env"
    env_file.write_text(f"SHC_STATE_DIR={ignored_dir}\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SECRET_KEY", "explicit-secret-key-from-env")

    settings = Settings(_env_file=str(env_file))
    monkeypatch.setattr("backend.core.config.settings", settings)

    assert settings.SECRET_KEY == "explicit-secret-key-from-env"
    # No file should be created in the ignored directory
    assert not (ignored_dir / "secret_key").exists()


# ---------------------------------------------------------------------------
# Issue #65: Regression tests for sentinel fix (DEFECT 1)
# ---------------------------------------------------------------------------


def test_resolve_secret_key_no_arg_uses_configured_state_dir(tmp_path, monkeypatch):
    """
    DEFECT 1 REGRESSION: _resolve_secret_key() with no argument must resolve
    under the CONFIGURED state dir (from global settings), not DEFAULT_STATE_DIR.

    Mutation that breaks this: _resolve_secret_key defaulting to None instead of
    the _UNSET sentinel, or _state_dir treating None as "explicit override".
    """
    configured_dir = tmp_path / "configured-state-dir"
    configured_dir.mkdir()

    # Create a Settings instance with SHC_STATE_DIR set
    test_settings = Settings(
        _env_file=None, SECRET_KEY="", SHC_STATE_DIR=str(configured_dir)
    )
    monkeypatch.setattr("backend.core.config.settings", test_settings)

    # Call _resolve_secret_key with NO argument (uses sentinel default)
    from backend.core.config import _resolve_secret_key

    key = _resolve_secret_key()

    # Key should be generated and written to configured_dir, NOT DEFAULT_STATE_DIR
    assert len(key) == 43
    key_file = configured_dir / "secret_key"
    assert (
        key_file.exists()
    ), f"Key should be written to {configured_dir}, not DEFAULT_STATE_DIR"
    assert key_file.read_text() == key
    # Ensure DEFAULT_STATE_DIR was not used
    default_key_file = DEFAULT_STATE_DIR / "secret_key"
    assert not default_key_file.exists() or default_key_file.read_text() != key


def test_resolve_secret_key_explicit_override(tmp_path, monkeypatch):
    """
    DEFECT 1 REGRESSION: An explicit shc_state_dir= must override the global setting.

    Mutation that breaks this: _state_dir not respecting explicit string argument.
    """
    global_dir = tmp_path / "global-state-dir"
    global_dir.mkdir()
    override_dir = tmp_path / "override-state-dir"
    override_dir.mkdir()

    # Global settings points to global_dir, but SECRET_KEY is set to avoid
    # key generation during Settings construction
    test_settings = Settings(
        _env_file=None, SECRET_KEY="test-key", SHC_STATE_DIR=str(global_dir)
    )
    monkeypatch.setattr("backend.core.config.settings", test_settings)

    from backend.core.config import _resolve_secret_key

    # Explicit override should write to override_dir
    key = _resolve_secret_key(shc_state_dir=str(override_dir))

    override_key_file = override_dir / "secret_key"
    assert override_key_file.exists(), f"Key should be written to {override_dir}"
    assert override_key_file.read_text() == key
    # Global dir should not have the key (no generation occurred there)
    global_key_file = global_dir / "secret_key"
    assert not global_key_file.exists()


def test_resolve_secret_key_explicit_none_uses_default(tmp_path, monkeypatch):
    """
    DEFECT 1 REGRESSION: _resolve_secret_key(shc_state_dir=None) explicitly passed
    as None uses DEFAULT_STATE_DIR, NOT the global settings.

    Mutation that breaks this: _state_dir treating None as "read from global settings"
    instead of "no configured value".
    """
    configured_dir = tmp_path / "configured-state-dir"
    configured_dir.mkdir()

    # Redirect DEFAULT_STATE_DIR to a temp path so we never touch the real one.
    fake_default = tmp_path / "fake-default"
    monkeypatch.setattr("backend.core.config.DEFAULT_STATE_DIR", fake_default)

    # Global settings has SHC_STATE_DIR set
    test_settings = Settings(
        _env_file=None, SECRET_KEY="", SHC_STATE_DIR=str(configured_dir)
    )
    monkeypatch.setattr("backend.core.config.settings", test_settings)

    from backend.core.config import _resolve_secret_key

    # Call with explicit None - should use DEFAULT_STATE_DIR (now redirected
    # to fake_default via monkeypatch)
    key_none = _resolve_secret_key(shc_state_dir=None)
    fake_key_file = fake_default / "secret_key"
    assert fake_key_file.exists(), "Explicit None should use DEFAULT_STATE_DIR"
    assert fake_key_file.read_text().strip() == key_none

    # Clean up for second call
    fake_key_file.unlink()

    # Call with no argument (sentinel) - should read from global settings
    key_omitted = _resolve_secret_key()
    key_omitted_file = configured_dir / "secret_key"
    assert key_omitted_file.exists(), "Omitted should read from global settings"
    assert key_omitted_file.read_text().strip() == key_omitted


# ---------------------------------------------------------------------------
# Issue #58: .env file discovery anchored to repository root.
# ---------------------------------------------------------------------------


def test_env_file_independent_of_cwd(tmp_path, monkeypatch):
    """T7 / AC-1, AC-2: .env at repo root is loaded from any CWD."""
    # Create a temp repo structure with a .env at its root
    repo_root = tmp_path / "fake-repo"
    repo_root.mkdir()
    (repo_root / ".env").write_text("SHARED_TOKEN=from-repo-root-env\n")

    # Two different launch directories
    cwd1 = tmp_path / "launch-from-here"
    cwd2 = tmp_path / "launch-from-there"
    cwd1.mkdir()
    cwd2.mkdir()

    env_file = str(repo_root / ".env")

    for cwd in (cwd1, cwd2):
        monkeypatch.chdir(cwd)
        s = Settings(_env_file=env_file, SECRET_KEY="test")
        assert s.SHARED_TOKEN == "from-repo-root-env"
        # Launch directory stays clean
        assert list(cwd.iterdir()) == []


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


# ---------------------------------------------------------------------------
# Issue #67: _resolve_secret_key() resilience — handle non-UTF-8 secret file.
# ---------------------------------------------------------------------------


class TestResolveSecretKeyUnicode:
    """Tests for UnicodeDecodeError handling and atomic write."""

    def test_corrupt_utf8_replaced(self, tmp_path, monkeypatch, caplog):
        """T1 / AC-1, AC-2, AC-8: Corrupt UTF-8 file is replaced with valid key."""
        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        # Write invalid UTF-8 bytes
        (state_dir / "secret_key").write_bytes(b"\xff\xfe\xfd")

        with caplog.at_level(logging.WARNING, logger="backend.core.config"):
            key = _resolve_secret_key(shc_state_dir=str(state_dir))

        # Key must be valid URL-safe base64 (43 chars for token_urlsafe(32))
        assert len(key) == 43
        # File must now contain the new key
        assert (state_dir / "secret_key").read_text() == key
        # Warning must be logged about the decode failure
        assert any("invalid UTF-8" in r.getMessage() for r in caplog.records)

    def test_empty_file_replaced(self, tmp_path, monkeypatch):
        """T2 / AC-3: Empty file is treated as absent and replaced."""
        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        (state_dir / "secret_key").write_text("")

        key = _resolve_secret_key(shc_state_dir=str(state_dir))

        assert (state_dir / "secret_key").read_text() == key
        assert len(key) == 43

    def test_valid_key_reused(self, tmp_path, monkeypatch):
        """T3 / AC-4: Valid existing key is reused (no regression)."""
        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        valid = "valid_urlsafe_base64_key_that_is_long_enough"
        (state_dir / "secret_key").write_text(valid)

        k1 = _resolve_secret_key(shc_state_dir=str(state_dir))
        k2 = _resolve_secret_key(shc_state_dir=str(state_dir))

        assert k1 == k2 == valid

    def test_published_key_rotated(self, tmp_path, monkeypatch, caplog):
        """T4 / AC-5: Published key (in PUBLISHED_KEY_SHA256) is rotated."""
        import backend.core.config as config_module

        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        # Use a test key and add its digest to the published set for this test
        published = "test_published_key_that_is_long_enough_to_be_valid"
        published_digest = config_module._digest_secret_key(published)
        # Temporarily add to the frozenset by patching the module variable
        original_published = config_module.PUBLISHED_KEY_SHA256
        config_module.PUBLISHED_KEY_SHA256 = frozenset(
            [*original_published, published_digest]
        )
        try:
            (state_dir / "secret_key").write_text(published)

            with caplog.at_level(logging.WARNING, logger="backend.core.config"):
                key = _resolve_secret_key(shc_state_dir=str(state_dir))

            assert key != published
            assert len(key) == 43
            assert "published in git history" in caplog.text
        finally:
            config_module.PUBLISHED_KEY_SHA256 = original_published

    def test_atomic_write_no_partial_file(self, tmp_path, monkeypatch):
        """T8 / AC-6: Write uses atomic pattern (temp file + os.replace)."""
        state_dir = tmp_path / ".shc"
        state_dir.mkdir()

        key = _resolve_secret_key(shc_state_dir=str(state_dir))

        content = (state_dir / "secret_key").read_text()
        assert content == key
        # Temp file should not exist after atomic rename
        assert not (state_dir / "secret_key.tmp").exists()

    def test_no_secret_in_logs(self, tmp_path, monkeypatch, caplog):
        """T9 / AC-7: No secret key material appears in log output."""
        state_dir = tmp_path / ".shc"
        state_dir.mkdir()

        with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
            key = _resolve_secret_key(shc_state_dir=str(state_dir))

        log_text = caplog.text
        # The generated key must not appear in logs
        assert key not in log_text
        # Also check for partial key material (first/last 10 chars)
        assert key[:10] not in log_text
        assert key[-10:] not in log_text
        # token_urlsafe should not appear in logs
        assert "token_urlsafe" not in log_text

    def test_permissions_600(self, tmp_path, monkeypatch):
        """AC-9: File permissions are 0o600 on new and replaced keys."""
        import sys

        state_dir = tmp_path / ".shc"
        state_dir.mkdir()

        _resolve_secret_key(shc_state_dir=str(state_dir))

        # Permission check only meaningful on POSIX
        if sys.platform != "win32":
            mode = (state_dir / "secret_key").stat().st_mode & 0o777
            assert mode == 0o600

    def test_missing_file_generates_key(self, tmp_path, monkeypatch):
        """T5: Missing file generates a new key."""
        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        # Ensure file doesn't exist
        key_file = state_dir / "secret_key"
        assert not key_file.exists()

        key = _resolve_secret_key(shc_state_dir=str(state_dir))

        assert key_file.exists()
        assert key_file.read_text() == key
        assert len(key) == 43

    def test_permission_denied_read_generates_new_key(
        self, tmp_path, monkeypatch, caplog
    ):
        """T6: Permission denied on read generates a new key."""
        import sys

        if sys.platform == "win32":
            pytest.skip("Permission test not reliable on Windows")

        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        key_file = state_dir / "secret_key"
        key_file.write_text("existing_valid_key_long_enough")
        # Remove read permission
        key_file.chmod(0o000)

        try:
            with caplog.at_level(logging.WARNING, logger="backend.core.config"):
                key = _resolve_secret_key(shc_state_dir=str(state_dir))
            # Should generate a new key (not the old one)
            assert len(key) == 43
            assert key != "existing_valid_key_long_enough"
            # The file should now contain the new key (write succeeded
            # because dir has write perm)
            assert key_file.read_text() == key
        finally:
            # Restore permissions for cleanup
            key_file.chmod(0o600)

    def test_permission_denied_write_uses_in_memory_key(
        self, tmp_path, monkeypatch, caplog
    ):
        """T7: Permission denied on write logs warning and uses in-memory key."""
        import sys

        if sys.platform == "win32":
            pytest.skip("Permission test not reliable on Windows")

        state_dir = tmp_path / ".shc"
        state_dir.mkdir()
        # Remove write permission on directory
        state_dir.chmod(0o555)

        try:
            with caplog.at_level(logging.WARNING, logger="backend.core.config"):
                key = _resolve_secret_key(shc_state_dir=str(state_dir))
            # Should generate a key in memory
            assert len(key) == 43
            # Should log a warning about OSError during write
            assert any("Could not persist" in r.getMessage() for r in caplog.records)
            # File should not exist (write failed)
            assert not (state_dir / "secret_key").exists()
        finally:
            # Restore permissions for cleanup
            state_dir.chmod(0o700)


# ---------------------------------------------------------------------------
# Issue #96: Heartbeat contract settings validation
# ---------------------------------------------------------------------------


def test_heartbeat_check_interval_less_than_timeout():
    """AC-2: Startup validation fails if check_interval >= timeout."""
    # check_interval == timeout should fail
    with pytest.raises(ValueError, match="must be <"):
        Settings(
            _env_file=None,
            SECRET_KEY="test",
            HEARTBEAT_TIMEOUT_SECONDS=90,
            HEARTBEAT_CHECK_INTERVAL_SECONDS=90,
        )

    # check_interval > timeout should fail
    with pytest.raises(ValueError, match="must be <"):
        Settings(
            _env_file=None,
            SECRET_KEY="test",
            HEARTBEAT_TIMEOUT_SECONDS=90,
            HEARTBEAT_CHECK_INTERVAL_SECONDS=120,
        )

    # check_interval < timeout should succeed
    settings = Settings(
        _env_file=None,
        SECRET_KEY="test",
        HEARTBEAT_TIMEOUT_SECONDS=90,
        HEARTBEAT_CHECK_INTERVAL_SECONDS=30,
    )
    assert settings.HEARTBEAT_TIMEOUT_SECONDS == 90
    assert settings.HEARTBEAT_CHECK_INTERVAL_SECONDS == 30


def test_heartbeat_defaults():
    """AC-1: Default heartbeat settings are 90 and 30."""
    settings = Settings(_env_file=None, SECRET_KEY="test")
    assert settings.HEARTBEAT_TIMEOUT_SECONDS == 90
    assert settings.HEARTBEAT_CHECK_INTERVAL_SECONDS == 30
