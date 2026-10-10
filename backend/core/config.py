"""
Configuration module for the Scientific Home Cluster Backend.
Uses Pydantic v2 BaseSettings for environment variable management.
"""

import hashlib
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

# SHA-256 digests of the JWT signing keys that were committed to this public
# repository and are therefore known to anyone who has cloned it:
#
#   .shc/secret_key              added in 2d1a8cb (issue #24)
#   backend/.shc/secret_key      added in 08f1f27 (issue #20)
#
# Both files are untracked as of issue #56, but untracking does not unpublish:
# the blobs stay in history, in every fork and in every mirror, and the live
# one is .shc/secret_key - `_state_dir()` resolves there for every working
# directory since #34, so a default checkout was signing with a public key.
# _resolve_secret_key() replaces any key whose digest appears here and logs a
# WARNING, which is how a running deployment is actually rotated rather than
# merely warned about in a README nobody reads.
#
# Digests only, never key values: a digest cannot sign anything, so shipping it
# is free. Computed over the *stripped* text of the checked-out file, not over
# `git show` output - core.autocrlf turns the 43-byte blob into 45 bytes with a
# trailing \r\n on the way out of a pipe, and a digest taken over that matches
# nothing and would disable rotation silently (issue #56).
PUBLISHED_KEY_SHA256: Final[frozenset[str]] = frozenset(
    {
        # .shc/secret_key
        "8b5cb844a8beed1cfaa320a1443410de4d4c375d01af8e8d7083b48d9ad56952",
        # backend/.shc/secret_key
        "794185eb6dfdc1fa7362a89f1b51b83265777c2c6ef2bae1298a13a7c256c00b",
    }
)

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
        version = data.get("project", {}).get("version")
        if isinstance(version, str):
            return version
        return "0.1.0"
    except Exception:
        return "0.1.0"


def _digest_secret_key(value: str) -> str:
    """
    SHA-256 of a signing key, as hex.

    Takes the same string the reader produces - the stripped file text - so the
    comparison against PUBLISHED_KEY_SHA256 cannot be thrown off by a trailing
    newline the platform added (issue #56).
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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

    A persisted key whose digest is in PUBLISHED_KEY_SHA256 is treated as
    absent: two of them were committed to this public repository, so anything
    signing with one is signing with a credential the world can read. It is
    replaced, not merely reported, because the code that starts the process runs
    on every deployment while a README warning reaches nobody. An explicit
    SECRET_KEY is never rotated - it is not persisted, so it cannot be a
    published key, and an operator who set it owns it (issue #56).
    """
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured

    state_path = Path(_state_dir()) / "secret_key"
    try:
        existing = state_path.read_text(encoding="utf-8").strip()
        if existing and _digest_secret_key(existing) not in PUBLISHED_KEY_SHA256:
            # Steady state: a normal restart reads the key back silently. A log
            # line here would be noise on every boot.
            return existing
        published = bool(existing)
    except OSError:
        published = False

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

    # Log the absolute location, not just the configured one: SHC_STATE_DIR is
    # used verbatim and may be relative, and "which file is my signing key?"
    # is unanswerable from a relative line (issue #56, mirroring the database
    # path log in backend/core/database.py).
    absolute_path = state_path if state_path.is_absolute() else Path.cwd() / state_path

    if published:
        # WARNING, not INFO, on purpose. This runs at import of
        # backend.core.config, and only backend/main.py configures logging -
        # it calls logging.basicConfig (main.py:31) immediately before importing
        # this module, precisely so the INFO generation line has a handler. Every
        # other entrypoint (backend.core.security, backend.core.deps,
        # backend.core.database, the API routers, the store, the CLI, pytest)
        # imports it with root unconfigured, where the default WARNING level
        # drops an INFO record before it is created. logging.lastResort emits
        # unhandled WARNING and above no matter how the process was started, so
        # the one line an operator must not miss is the one that survives every
        # way of getting here.
        logger.warning(
            "The JWT signing key at %s was published in git history, so anyone "
            "can read it; it has been replaced with a newly generated key. "
            "Previously issued tokens are now invalid - mint a new one with "
            "POST /api/v1/auth/token after setting SHARED_TOKEN (issue #56).",
            absolute_path,
        )
    else:
        logger.info("Generated a new JWT signing key at %s.", absolute_path)
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
    ) -> Union[str, List[Union[str, AnyHttpUrl]]]:
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
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
    )

    def model_post_init(self, _context) -> None:
        # SECRET_KEY defaults to empty so that pydantic does not shadow the
        # environment variable; fill it in from the persisted key.
        if not self.SECRET_KEY:
            self.SECRET_KEY = _resolve_secret_key()


settings = Settings()
