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
from typing import List, Union, Optional

logger = logging.getLogger(__name__)


def get_version_from_pyproject() -> str:
    """Read version from pyproject.toml."""
    try:
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
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
    """Directory for locally persisted server state."""
    return os.environ.get("SHC_STATE_DIR", ".shc")


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

    # Database configuration
    DATABASE_URL: str = "sqlite:///./scientific_home_cluster.db"
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
