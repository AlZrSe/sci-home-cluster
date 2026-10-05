"""
Configuration module for the Scientific Home Cluster Backend.
Uses Pydantic v2 BaseSettings for environment variable management.
"""

import logging
import os
import secrets
import tomllib
from pathlib import Path
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Final, List, Optional, Union

logger = logging.getLogger(__name__)

# The repository root, derived from this file's own location rather than from
# the current working directory. This is the one anchor the whole module shares:
# a library import must not depend on the CWD (issue #34), and with the
# documented `pip install -e .` install the package location *is* the checkout
# location, so it points at the tree the developer is editing.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# The one canonical database: an absolute path under the repo root, identical
# from any working directory. Kept a plain str (never a URL object) because
# database.py, alembic/env.py and alembic.ini all match on the literal
# "sqlite://" prefix and rewrite it to "sqlite+aiosqlite://".
#
# Path.as_posix() is what makes this correct on Windows, where the SQLAlchemy
# form is `sqlite:///C:/.../data/scientific_home_cluster.db`.
CANONICAL_DATABASE_PATH: Final[Path] = (
    _REPO_ROOT / "data" / "scientific_home_cluster.db"
)
DEFAULT_DATABASE_URL: Final[str] = "sqlite:///" + CANONICAL_DATABASE_PATH.as_posix()

# Where the JWT signing key is persisted when SECRET_KEY is not configured.
# Absolute for the same reason as the database path: the previous default was
# the literal ".shc", so `import backend.core.config` wrote ./.shc/secret_key
# into whatever directory the process happened to be launched from.
DEFAULT_STATE_DIR: Final[Path] = _REPO_ROOT / ".shc"

_SQLITE_DIALECT: Final[str] = "sqlite"


def sqlite_path_from_url(url: str) -> Optional[Path]:
    """
    The filesystem path a SQLite URL points at, or None if it has none.

    Handles the shapes SQLAlchemy accepts, which differ only in how many
    slashes precede the path:

      sqlite:///:memory:           -> None (not a file)
      sqlite:///./local.db         -> ./local.db            (relative)
      sqlite:////srv/x.db          -> /srv/x.db             (POSIX absolute)
      sqlite:///C:/data/x.db       -> C:/data/x.db          (Windows absolute)

    The dialect may carry a driver ("sqlite+aiosqlite"), because
    resolve_database_url() rewrites the scheme before anything else reads the
    URL back.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return None
    if scheme.split("+", 1)[0] != _SQLITE_DIALECT:
        return None
    # A POSIX absolute path keeps its own leading slash after the separator,
    # so exactly one slash is consumed here.
    if rest.startswith("/"):
        rest = rest[1:]
    if not rest or rest.startswith(":memory:"):
        return None
    return Path(rest)


def absolute_sqlite_path(url: str) -> Optional[Path]:
    """
    The absolute location a SQLite URL resolves to, or None for non-file URLs.

    A relative URL is resolved against the current working directory, exactly
    as SQLite itself would, so a caller that reports or creates the path cannot
    disagree with the driver about which file is meant.
    """
    path = sqlite_path_from_url(url)
    if path is None:
        return None
    return path if path.is_absolute() else Path.cwd() / path


def get_version_from_pyproject() -> str:
    """Read version from pyproject.toml."""
    try:
        pyproject_path = _REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.1.0")
    except Exception:
        return "0.1.0"


def _resolve_secret_key() -> str:
    """
    Resolve the JWT signing key.

    Order of preference:
      1. the SECRET_KEY environment variable (or .env);
      2. a key persisted next to the database, so restarting the backend
         does not log every user out;
      3. a freshly generated key, persisted for next time.

    This used to be secrets.token_urlsafe(32) inline as the field default,
    which meant a new key on every process start and therefore every
    previously issued token became invalid on each restart.
    """
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured

    state_path = Path(_state_dir()) / "secret_key"
    try:
        existing = state_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    generated = secrets.token_urlsafe(32)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(generated, encoding="utf-8")
        # Restrict to the owner: this file signs every access token.
        state_path.chmod(0o600)
    except OSError:
        logger.warning(
            "Could not persist the generated SECRET_KEY to %s; "
            "tokens will not survive a restart.",
            state_path,
        )
    return generated


def _state_dir() -> str:
    """
    Directory for locally persisted server state.

    SHC_STATE_DIR still wins, and is used verbatim so a relative value stays
    relative (it is the user's explicit choice). Unset, the directory is
    absolute and anchored on the package location: the previous default was the
    literal ".shc", which meant the directory - and the signing key written into
    it - moved with the working directory (issue #34).
    """
    configured = os.environ.get("SHC_STATE_DIR")
    if configured:
        return configured
    return str(DEFAULT_STATE_DIR)


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Scientific Home Cluster API"
    VERSION: str = get_version_from_pyproject()

    # Security settings
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    SHARED_TOKEN: Optional[str] = None  # Shared bearer token for API access

    # Syncthing configuration
    SYNCTHING_ROOT: str = "/tmp/syncthing"

    # Database configuration.
    #
    # The default is an absolute path under the repo root, NOT a relative one:
    # a relative SQLite path is resolved against the process working directory,
    # so `uvicorn backend.main:app` from the repo root and `uvicorn main:app`
    # from backend/ used to open two different files and neither the logs nor
    # /health could say which (issue #34). An explicit DATABASE_URL still wins,
    # including a relative one - that is warned about below, not rejected.
    DATABASE_URL: str = DEFAULT_DATABASE_URL
    # Connection pool strategy: "pooled" (default) or "null".
    # "null" opens a fresh connection per session, which the test suite uses
    # so that connections are never shared between event loops.
    DB_POOL: str = "pooled"
    # Seed the 10 demo jobs / 4 demo nodes (a port of the frontend mock
    # server) into an empty database on first use.
    #
    # Off by default: a production database must start empty, which is what
    # docs/api-server-core-spec.md already requires ("NO seed data in
    # migrations"). Opt in for local development and for the test suite,
    # which asserts on the demo dataset.
    #
    # Startup-only. Flipping this at runtime would re-seed a database that
    # is meant to stay empty, which is the bug this flag exists to close.
    SEED_DEMO_DATA: bool = False

    @field_validator("DATABASE_URL")
    @classmethod
    def warn_on_relative_database_url(cls, v: str) -> str:
        """
        Warn - but do not reject - an explicitly configured relative URL.

        Rejecting would turn a silently-wrong configuration into a crash on
        upgrade for anyone who set a relative DATABASE_URL to point at a
        database on their Syncthing volume; the whole point of issue #34 is to
        stop *silent* divergence, so it is named out loud once instead. The
        default itself cannot reach this branch: it is asserted absolute by
        backend/tests/unit/test_config.py, so the relative form cannot come back
        through the front door.

        Runs at construction, which is a field validator's privilege and the
        earliest point a user can be told - the module-level `settings` is built
        during import, so by the time anything reads it the warning has fired.
        """
        path = sqlite_path_from_url(v)
        if path is not None and not path.is_absolute():
            logger.warning(
                "DATABASE_URL %r is relative, so SQLite resolves it against the "
                "current working directory and the database you get depends on "
                "where this process was started. It currently resolves to %s. "
                "Set DATABASE_URL to an absolute path (the default is %s), or "
                "unset it to use that default.",
                v,
                Path.cwd() / path,
                DEFAULT_DATABASE_URL,
            )
        return v

    # CORS origins
    BACKEND_CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(
        cls, v: Union[str, List[Union[str, AnyHttpUrl]]]
    ) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    # Logging
    LOG_LEVEL: str = "INFO"

    # Localhost bypass for development
    LOCALHOST_BYPASS: bool = True

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    def model_post_init(self, _context) -> None:
        # SECRET_KEY defaults to empty so that pydantic does not shadow the
        # environment variable; fill it in from the persisted key.
        if not self.SECRET_KEY:
            self.SECRET_KEY = _resolve_secret_key()


settings = Settings()
