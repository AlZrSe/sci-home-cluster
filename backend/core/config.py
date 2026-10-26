"""
Configuration module for the Scientific Home Cluster Backend.
Uses Pydantic v2 BaseSettings for environment variable management.
"""

import hashlib
import logging
import os
import secrets
import tomllib
from enum import Enum
from pathlib import Path
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Final, List, Optional, Union

from shared.file_ops.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# The repository root, derived from this file's own location rather than from
# the current working directory. This is the one anchor the whole module shares:
# a library import must not depend on the CWD (issue #34), and with the
# documented `pip install -e .` install the package location *is* the checkout
# location, so it points at the tree the developer is editing.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# Default directory for the SQLite database file. Can be overridden via
# SHC_DATA_DIR environment variable (Settings field below) to point at a
# host-mounted volume at any path. The default mirrors the historical
# location under the repo root so local development keeps working unchanged.
DEFAULT_DATA_DIR: Final[Path] = _REPO_ROOT / "data"

# The one canonical database: an absolute path under the repo root, identical
# from any working directory. Kept a plain str (never a URL object) because
# database.py, alembic/env.py and alembic.ini all match on the literal
# "sqlite://" prefix and rewrite it to "sqlite+aiosqlite://".
#
# Path.as_posix() is what makes this correct on Windows, where the SQLAlchemy
# form is `sqlite:///C:/.../data/scientific_home_cluster.db`.
CANONICAL_DATABASE_PATH: Final[Path] = DEFAULT_DATA_DIR / "scientific_home_cluster.db"
DEFAULT_DATABASE_URL: Final[str] = "sqlite:///" + CANONICAL_DATABASE_PATH.as_posix()

# Where the JWT signing key is persisted when SECRET_KEY is not configured.
# Absolute for the same reason as the database path: the previous default was
# the literal ".shc", so `import backend.core.config` wrote ./.shc/secret_key
# into whatever directory the process happened to be launched from.
DEFAULT_STATE_DIR: Final[Path] = _REPO_ROOT / ".shc"

# Where a generated SHARED_TOKEN is persisted, for the same reason
# SECRET_KEY is generated rather than demanded. See _resolve_shared_token().
SHARED_TOKEN_FILENAME: Final[str] = "shared_token"

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


class LocalhostBypassMode(str, Enum):
    """
    Enum for localhost bypass modes.

    - DISABLED: Bypass never activates (secure default for production).
    - SOCKET: Bypass activates only for loopback socket peers
      (new default for development).
    - HOST_HEADER: Legacy behavior (client-controlled Host header)
      - DEPRECATED.
    """

    DISABLED = "disabled"
    SOCKET = "socket"
    HOST_HEADER = "host_header"


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


# Sentinel to distinguish "not passed" from "passed as None/str"
class _Unset:
    """Sentinel for 'parameter not passed'."""

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _Unset()


def _write_state_secret(state_path: Path, value: str) -> Optional[str]:
    """
    Write a generated secret to ``state_path`` atomically and owner-only.

    Returns the absolute path on success, or None if it could not be written -
    in which case the caller still has the value in memory and must say so,
    because the whole point of persisting is that it survives a restart.

    Atomic write: temp file -> close (which flushes) -> ``os.replace``. The
    rename is atomic on POSIX and Windows (since Python 3.3), so a crash or a
    concurrent reader never sees a half-written secret. ``chmod`` runs on the
    temp file BEFORE the rename so the final file is never briefly
    world-readable.

    Uses shared atomic_write utility with Windows-compatible retry logic
    for PermissionError when destination is open (WinError 5).
    """
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(state_path, value, mode=0o600)
    except OSError:
        return None

    # Log the absolute location, not just the configured one: SHC_STATE_DIR is
    # used verbatim and may be relative, and "which file is my secret?" is
    # unanswerable from a relative line (issue #56, mirroring the database
    # path log in backend/core/database.py).
    return str(state_path if state_path.is_absolute() else Path.cwd() / state_path)


def _resolve_secret_key(shc_state_dir: Union[Optional[str], _Unset] = _UNSET) -> str:
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
    published key, and an operator who sets it owns it (issue #56).

    Non-UTF-8 or corrupt key files are treated as absent and replaced with
    a newly generated key (issue #67). The write is atomic via a temp file
    and os.replace to avoid leaving a partially written key.
    """
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured

    state_path = Path(_state_dir(shc_state_dir)) / "secret_key"
    try:
        # Read as bytes first to catch UnicodeDecodeError explicitly.
        raw = state_path.read_bytes()
        existing = raw.decode("utf-8").strip()
        if existing and _digest_secret_key(existing) not in PUBLISHED_KEY_SHA256:
            # Steady state: a normal restart reads the key back silently. A log
            # line here would be noise on every boot.
            return existing
        published = bool(existing)
    except (OSError, UnicodeDecodeError) as e:
        if isinstance(e, UnicodeDecodeError):
            logger.warning(
                "The JWT signing key at %s contains invalid UTF-8; "
                "it will be replaced with a newly generated key.",
                state_path,
            )
        published = False

    generated = secrets.token_urlsafe(32)
    absolute_path = _write_state_secret(state_path, generated)
    if absolute_path is None:
        logger.warning(
            "Could not persist the generated SECRET_KEY to %s; "
            "tokens will not survive a restart.",
            state_path,
        )
        return generated

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


def _resolve_shared_token(shc_state_dir: Union[Optional[str], _Unset] = _UNSET) -> str:
    """
    Resolve the shared bearer token, generating and persisting one if needed.

    Order of preference:

      1. an explicitly configured ``SHARED_TOKEN`` - the caller has already
         checked, and this is only reached when there was none;
      2. a token persisted under ``SHC_STATE_DIR``, so a restart does not
         invalidate it;
      3. a freshly generated one, persisted for next time.

    Why this exists. ``SHARED_TOKEN`` used to be ``None`` until an operator set
    it, and ``POST /api/v1/auth/token`` answered ``SHARED_TOKEN_NOT_CONFIGURED``
    to every login (issue #56). That is a correct refusal of a *guessable* token
    and a useless answer for a deployment that simply has not been configured
    yet: the cluster is running, the dashboard is open, and nothing works, with
    no hint what to do. The same already happened to the JWT signing key, which
    has generated and persisted its own since #24.

    Generating here means ``docker compose up -d`` works on a machine that has
    never run this project before. The cost is that a typo in an intended
    ``SHARED_TOKEN`` is no longer a startup failure - it produces a working
    cluster with a token nobody else has, which the WARNING below exists to
    surface.
    """
    state_path = Path(_state_dir(shc_state_dir)) / SHARED_TOKEN_FILENAME
    try:
        existing = state_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        existing = ""

    if existing:
        # Steady state: a restart reads it back silently, exactly as the
        # signing key does. A log line here would be noise on every boot.
        return existing

    generated = secrets.token_urlsafe(32)
    written = _write_state_secret(state_path, generated)
    if written is None:
        logger.warning(
            "No SHARED_TOKEN is configured and a generated one could not be "
            "persisted to %s; it will change on every restart. Set SHARED_TOKEN "
            "explicitly to keep it stable.",
            state_path,
        )
        return generated

    # WARNING, not INFO, and never the value. The operator needs to know two
    # things: that nothing was configured, and where the generated token lives.
    # The token itself is a credential and does not belong in a log that is
    # routinely piped somewhere else.
    logger.warning(
        "SHARED_TOKEN is not configured, so one was generated and saved to %s. "
        "Read it with 'cat %s' and use it to mint a token at "
        "POST /api/v1/auth/token, or set SHARED_TOKEN yourself to choose one. "
        "Without it, login is unavailable - not merely inconvenient.",
        written,
        written,
    )
    return generated


def _resolve_cookie_secret(shc_state_dir: Union[Optional[str], _Unset] = _UNSET) -> str:
    """
    Resolve the cookie signing secret, generating and persisting one if needed.

    Separate from JWT signing key for defense in depth - rotating this key
    invalidates all auth cookies without affecting JWT tokens.

    Order of preference:
      1. an explicitly configured ``AUTH_COOKIE_SECRET``
      2. a secret persisted under ``SHC_STATE_DIR``
      3. a freshly generated one, persisted for next time
    """
    state_path = Path(_state_dir(shc_state_dir)) / "cookie_secret"
    try:
        existing = state_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        existing = ""

    if existing:
        return existing

    generated = secrets.token_urlsafe(32)
    written = _write_state_secret(state_path, generated)
    if written is None:
        logger.warning(
            "Could not persist the generated AUTH_COOKIE_SECRET to %s; "
            "cookies will not survive a restart.",
            state_path,
        )
        return generated

    logger.info("Generated a new cookie signing secret at %s.", written)
    return generated


def _state_dir(shc_state_dir: Union[Optional[str], _Unset] = _UNSET) -> str:
    """
    Directory for locally persisted server state.

    SHC_STATE_DIR (from Settings, via .env or environment) wins, and is used
    verbatim so a relative value stays relative. Unset, the directory is
    absolute and anchored on the package location: the previous default was the
    literal ".shc", which meant the directory - and the signing key written into
    it - moved with the working directory (issue #34).

    The optional `shc_state_dir` parameter allows callers (e.g., model_post_init)
    to pass the value directly, avoiding a circular reference to the global
    `settings` instance during initialization. When called without the parameter
    (after initialization), it reads from the global `settings` instance.
    """
    # Explicit string override: use it verbatim (relative stays relative)
    if isinstance(shc_state_dir, str):
        return shc_state_dir

    # Omitted (sentinel): read from global settings if available
    if shc_state_dir is _UNSET:
        try:
            configured = settings.SHC_STATE_DIR
        except NameError:
            # During model_post_init, the global `settings` is not yet assigned.
            configured = None
        if configured:
            return configured
        return str(DEFAULT_STATE_DIR)

    # Explicit None: no configured value, fall back to default
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
    SYNCTHING_GUI_HOST: str = "127.0.0.1"
    SYNCTHING_GUI_PORT: int = 8384

    # Local server state directory (holds JWT signing key)
    SHC_STATE_DIR: Optional[str] = None

    # Data directory for SQLite database. Override to point at a host-mounted
    # volume. Defaults to DEFAULT_DATA_DIR (repo_root/data). In the container
    # image this should point at the mounted volume path.
    SHC_DATA_DIR: Optional[str] = None

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

    # Static frontend serving.
    #
    # Off by default. The backend is an API server; nothing about it should grow a
    # dependency on a frontend build being present. It is switched on in the
    # container image (docker/Dockerfile sets SERVE_FRONTEND=true), where the same
    # process serves both halves on one origin so the browser needs no CORS.
    #
    # FRONTEND_DIR points at the *client output* of a TanStack Start SPA build -
    # .output/public, which holds the hashed asset bundles plus the prerendered
    # `_shell.html`. Not at .output, which also holds the nitro server bundle this
    # process never runs.
    SERVE_FRONTEND: bool = False
    FRONTEND_DIR: str = str(_REPO_ROOT / "frontend" / ".output" / "public")

    # Lovable preview CORS opt-in
    ENABLE_LOVABLE_PREVIEW_CORS: bool = False

    @property
    def effective_cors_origins(self) -> list[str]:
        """
        Returns the effective CORS origins list, optionally including
        Lovable preview origins.

        When ENABLE_LOVABLE_PREVIEW_CORS is True, adds known Lovable preview
        origins to the base BACKEND_CORS_ORIGINS list. This is opt-in to avoid
        exposing authenticated responses to arbitrary Lovable previews in
        development.
        """
        origins = [str(origin) for origin in self.BACKEND_CORS_ORIGINS]
        if self.ENABLE_LOVABLE_PREVIEW_CORS:
            origins.extend(
                [
                    "https://preview.lovableproject.com",
                    # Additional specific preview URLs can be added via
                    # BACKEND_CORS_ORIGINS env var
                ]
            )
        return origins

    # Logging
    LOG_LEVEL: str = "INFO"

    # OpenTelemetry / observability. All optional; environment variables win
    # at runtime via backend.core.telemetry resolution.
    OTEL_SERVICE_NAME: Optional[str] = None
    ENVIRONMENT: str = "development"
    APP_VERSION: Optional[str] = None
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    OTEL_ENABLED: bool = True
    OTEL_SDK_DISABLED: bool = False
    # Localhost bypass for development
    LOCALHOST_BYPASS: bool = True
    LOCALHOST_BYPASS_MODE: Optional[LocalhostBypassMode] = None

    @field_validator("LOCALHOST_BYPASS_MODE", mode="before")
    @classmethod
    def _resolve_localhost_bypass_mode(
        cls, v: Union[str, LocalhostBypassMode, None]
    ) -> Optional[LocalhostBypassMode]:
        """
        Parse LOCALHOST_BYPASS_MODE from env/constructor.
        Returns None if not explicitly set, to be resolved in model_post_init.
        """
        if v is not None and v != "":
            if isinstance(v, str):
                try:
                    return LocalhostBypassMode(v.lower())
                except ValueError:
                    logger.warning(
                        "Invalid LOCALHOST_BYPASS_MODE=%r, defaulting to 'socket'", v
                    )
                    return LocalhostBypassMode.SOCKET
            return v
        return None

    @property
    def localhost_bypass_mode(self) -> LocalhostBypassMode:
        """
        Dynamically resolve the localhost bypass mode.
        This allows tests to patch LOCALHOST_BYPASS and have the mode recomputed.
        """
        if self.LOCALHOST_BYPASS_MODE is not None:
            return self.LOCALHOST_BYPASS_MODE
        if self.LOCALHOST_BYPASS is False:
            return LocalhostBypassMode.DISABLED
        return LocalhostBypassMode.SOCKET

    # Heartbeat contract (issue #96)
    HEARTBEAT_TIMEOUT_SECONDS: int = 90
    HEARTBEAT_CHECK_INTERVAL_SECONDS: int = 30

    @field_validator("HEARTBEAT_CHECK_INTERVAL_SECONDS")
    @classmethod
    def _check_heartbeat_interval_vs_timeout(cls, v: int, info) -> int:
        """Ensure check_interval < timeout so a healthy node is observed
        at least twice within the timeout."""
        timeout = info.data.get("HEARTBEAT_TIMEOUT_SECONDS")
        if timeout is not None and v >= timeout:
            raise ValueError(
                f"HEARTBEAT_CHECK_INTERVAL_SECONDS ({v}) must be < "
                f"HEARTBEAT_TIMEOUT_SECONDS ({timeout}), otherwise a healthy "
                f"node can be observed at delta >= timeout and flipped OFFLINE"
            )
        return v

    # Auth rate limiting configuration (issue #157)
    AUTH_RATE_LIMIT_BASE: int = 5
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 900
    AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS: int = 2
    AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS: int = 3600
    AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD: int = 11
    AUTH_RATE_LIMIT_LOCKOUT_SECONDS: int = 3600

    # Cookie-based authentication configuration (issue #158)
    AUTH_COOKIE_NAME: str = "shc_token"
    AUTH_COOKIE_MAX_AGE_SECONDS: int = 86400  # 24 hours
    AUTH_COOKIE_SECURE: bool = True
    AUTH_COOKIE_SAME_SITE: str = "strict"  # "strict", "lax", or "none"
    AUTH_COOKIE_SECRET: Optional[str] = None  # Separate signing key for cookies
    AUTH_WS_TICKET_TTL_SECONDS: int = 300  # 5 minutes

    # Content-Security-Policy configuration (issue #158)
    CSP_POLICY: str = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self' wss:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    CSP_REPORT_ONLY: bool = False

    # Interactive API documentation (Swagger UI / ReDoc / OpenAPI schema)
    #
    # Disabled by default in production for security (issue #161).
    # Enabled implicitly when ENVIRONMENT=development or ENVIRONMENT=test.
    # Can be explicitly overridden with ENABLE_API_DOCS=true/false.
    ENABLE_API_DOCS: bool = False

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
    )

    def model_post_init(self, _context) -> None:
        # SECRET_KEY defaults to empty so that pydantic does not shadow the
        # environment variable; fill it in from the persisted key.
        if not self.SECRET_KEY:
            self.SECRET_KEY = _resolve_secret_key(self.SHC_STATE_DIR)

        # Same treatment for SHARED_TOKEN, and for the same reason: a
        # deployment that has not been configured yet gets a working cluster
        # rather than a login that cannot succeed. An explicit SHARED_TOKEN
        # still wins - `not self.SHARED_TOKEN` is false for any real value, and
        # an operator who sets one never sees this path.
        if not self.SHARED_TOKEN:
            self.SHARED_TOKEN = _resolve_shared_token(self.SHC_STATE_DIR)

        # Cookie signing key (separate from JWT key for defense in depth)
        # Same pattern: generate and persist if not configured.
        if not self.AUTH_COOKIE_SECRET:
            self.AUTH_COOKIE_SECRET = _resolve_cookie_secret(self.SHC_STATE_DIR)

        # Resolve DATABASE_URL from SHC_DATA_DIR if provided and not explicitly
        # set to a non-default value. This allows mounting the database at any
        # host path via -v /host/path:/mnt/data -e SHC_DATA_DIR=/mnt/data
        if self.SHC_DATA_DIR and self.DATABASE_URL == DEFAULT_DATABASE_URL:
            from pathlib import Path

            data_dir = Path(self.SHC_DATA_DIR)
            db_path = data_dir / "scientific_home_cluster.db"
            self.DATABASE_URL = "sqlite:///" + db_path.as_posix()

        # Resolve LOCALHOST_BYPASS_MODE with backward compatibility
        # Priority:
        # 1. Explicit LOCALHOST_BYPASS_MODE (already set by validator if provided)
        # 2. LOCALHOST_BYPASS=false -> DISABLED
        # 3. LOCALHOST_BYPASS=true (default) -> SOCKET
        if self.LOCALHOST_BYPASS_MODE is None:
            if self.LOCALHOST_BYPASS is False:
                self.LOCALHOST_BYPASS_MODE = LocalhostBypassMode.DISABLED
            else:
                self.LOCALHOST_BYPASS_MODE = LocalhostBypassMode.SOCKET

        # Log localhost bypass configuration
        self._log_localhost_bypass_config()

    def _log_localhost_bypass_config(self) -> None:
        """Log localhost bypass configuration at startup."""
        mode = self.LOCALHOST_BYPASS_MODE
        if mode == LocalhostBypassMode.DISABLED:
            logger.info("Localhost authentication bypass is DISABLED")
            return

        # At this point mode is guaranteed to be non-None
        assert mode is not None
        logger.info(
            "Localhost authentication bypass is ENABLED (mode: %s). "
            "This is a developer convenience, not a security boundary.",
            mode.value,
        )

        if mode == LocalhostBypassMode.HOST_HEADER:
            logger.warning(
                "LOCALHOST_BYPASS_MODE=host_header is DEPRECATED and INSECURE: "
                "it trusts the client-supplied Host header. Use 'socket' mode instead."
            )

        # Log accepted loopback IPs for socket mode
        if mode == LocalhostBypassMode.SOCKET:
            loopback_ips = ["127.0.0.1", "::1", "::ffff:127.0.0.1"]
            logger.info(
                "Accepted loopback IPs for socket mode: %s", ", ".join(loopback_ips)
            )


settings = Settings()
