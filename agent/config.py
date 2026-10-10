"""
Configuration for the worker agent.

The config file is ``<SYNCTHING_ROOT>/nodes/agent.toml`` (issue #93 Q1). It
lives inside the replicated folder so one edit configures the whole fleet.
Everything below exists to make that file safe to replicate:

* the root is **resolved before the config path is derived**, so a ``~`` or
  relative ``SYNCTHING_ROOT`` finds the file instead of silently running on
  defaults (issue #93 D1);
* the layers are merged by hand, because pydantic-settings 2.0.0 has no TOML
  source and its init kwargs outrank the environment, which would invert the
  documented order silently (issue #93 R1);
* there is exactly one construction site, ``build_settings`` -- no module-level
  ``settings = AgentSettings()`` the way ``backend/core/config.py`` has one;
* ``NODE_ID`` and ``SYNCTHING_ROOT`` are **refused** in the file rather than
  ignored, because every node reads the same replicated bytes (issue #93 Q1
  Edge 2, NHD-3).
"""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from agent.logging_config import VALID_LOG_LEVELS
from agent.paths import resolve_root

#: Process exit codes. Defined here so issue #104's systemd unit inherits one
#: table instead of inventing a second (issue #93 R10).
EXIT_OK: Final[int] = 0
EXIT_CONFIG_ERROR: Final[int] = 1
EXIT_INTERNAL_ERROR: Final[int] = 2
EXIT_WATCHER_DEAD: Final[int] = 3
EXIT_SHUTDOWN_GRACE_EXPIRED: Final[int] = 4

#: The suffix is load-bearing. ``backend/services/syncthing_service.py`` routes
#: any ``nodes/*.yaml`` into node registration, so a config file named
#: ``agent.yaml`` would create a phantom node on every node in the fleet
#: (issue #93 Q1 Edge 1). Pinned by a test that calls the backend's live
#: ``_is_relevant_file``.
CONFIG_FILE_NAME: Final[str] = "agent.toml"
CONFIG_DIR_NAME: Final[str] = "nodes"

#: Closes path traversal through ``get_node_state_file``, which interpolates
#: the node id straight into a filename. It closes **traversal, not
#: impersonation**: per issue #92 the agent holds no identity, so a well-formed
#: node id is if anything easier to impersonate than a malformed one, and the
#: only real control is the Syncthing folder ACL (issue #93 R4).
NODE_ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
_NODE_ID_RE: Final[re.Pattern[str]] = re.compile(NODE_ID_PATTERN)

#: Interval used while waiting for the Syncthing folder to appear at startup.
FOLDER_RETRY_INTERVAL_S: Final[float] = 1.0

STATE_DIR_NAME: Final[str] = ".agent"

#: Env var name and TOML key -> settings field. The two are deliberately the
#: same spelling: a key an operator types into the shared file is the key they
#: would export. ``NODE_ID`` and ``SYNCTHING_ROOT`` are unprefixed because they
#: mean something outside the agent (issue #93 K6); the rest are ``AGENT_``
#: prefixed so a backend ``LOG_LEVEL=DEBUG`` cannot silently debug the agent
#: (issue #93 Q2).
_SOURCE_NAMES: Final[dict[str, str]] = {
    "NODE_ID": "NODE_ID",
    "SYNCTHING_ROOT": "SYNCTHING_ROOT",
    "AGENT_LOG_LEVEL": "LOG_LEVEL",
    "AGENT_SHUTDOWN_GRACE_S": "SHUTDOWN_GRACE_S",
    "AGENT_FOLDER_WATCH_INTERVAL_S": "FOLDER_WATCH_INTERVAL_S",
    "AGENT_FOLDER_RETRY_MAX_S": "FOLDER_RETRY_MAX_S",
    "AGENT_WATCHER_LIVENESS_INTERVAL_S": "WATCHER_LIVENESS_INTERVAL_S",
    "AGENT_STATE_DIR": "AGENT_STATE_DIR",
}

#: Names that mean something in the shared file but cannot be honoured there.
#: Each gets its own message rather than the generic unknown-key rejection,
#: because the same message reaches every node in the fleet (issue #93 NHD-3).
_FORBIDDEN_TOML_KEYS: Final[frozenset[str]] = frozenset({"NODE_ID", "SYNCTHING_ROOT"})


class ConfigError(ValueError):
    """An operator-facing configuration error. Always exits ``EXIT_CONFIG_ERROR``."""


class AgentSettings(BaseSettings):
    """The agent's complete configuration surface.

    Per issue #92 the agent holds **no credential**: there is no secret in its
    environment for a submitted command to inherit, and this model is the only
    place such a field could be added.
    """

    model_config = SettingsConfigDict(
        extra="forbid",
        case_sensitive=True,
        validate_assignment=False,
    )

    NODE_ID: str
    SYNCTHING_ROOT: Path
    LOG_LEVEL: str = "INFO"
    SHUTDOWN_GRACE_S: float = 30.0
    FOLDER_WATCH_INTERVAL_S: float = 30.0
    FOLDER_RETRY_MAX_S: float = 60.0
    WATCHER_LIVENESS_INTERVAL_S: float = 60.0
    AGENT_STATE_DIR: Path

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Refuse to read any source other than the hand-merged init kwargs.

        Returning ``(init_settings,)`` stops pydantic-settings re-reading the
        process environment behind the injected mapping, which is what keeps
        ``build_settings``' injected ``environ`` authoritative and makes the
        tests free of ``monkeypatch.setenv`` (issue #93 R1, US-5).
        """
        return (init_settings,)

    @field_validator("NODE_ID")
    @classmethod
    def _check_node_id(cls, value: str) -> str:
        if not _NODE_ID_RE.match(value):
            raise ValueError(
                f"NODE_ID {value!r} is not a valid node id; it must match "
                f"{NODE_ID_PATTERN} (letters, digits, '-' and '_', starting with "
                "a letter or digit). The id is interpolated into a filename in "
                "the Syncthing folder, so a '/' or '..' would let it escape."
            )
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        if value.upper() not in VALID_LOG_LEVELS:
            raise ValueError(
                f"unknown log level {value!r}; valid levels are "
                f"{', '.join(VALID_LOG_LEVELS)}"
            )
        return value.upper()

    @field_validator(
        "SHUTDOWN_GRACE_S",
        "FOLDER_WATCH_INTERVAL_S",
        "FOLDER_RETRY_MAX_S",
        "WATCHER_LIVENESS_INTERVAL_S",
    )
    @classmethod
    def _check_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(f"must be greater than 0, got {value}")
        return value

    @field_validator("SYNCTHING_ROOT", "AGENT_STATE_DIR")
    @classmethod
    def _check_resolved_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError(
                f"must be an absolute path, got {value!r}; build_settings "
                "resolves the root before this model sees it"
            )
        return value


def build_parser() -> argparse.ArgumentParser:
    """The agent's whole command-line surface.

    There is deliberately **no ``--config`` flag** (issue #93 Q1): the location
    is derived from the resolved root, which keeps a CWD-relative search out of
    the codebase entirely and makes the same command answer the same way from
    any directory.
    """
    parser = argparse.ArgumentParser(
        prog="run-agent",
        description="Scientific Home Cluster Worker Agent",
    )
    parser.add_argument(
        "--node-id",
        default=None,
        help=(
            "Unique identifier for this node. Required, and not settable in "
            "nodes/agent.toml: every node reads that same replicated file."
        ),
    )
    parser.add_argument(
        "--syncthing-root",
        default=None,
        help=(
            "Path to the Syncthing shared folder. Required; may also come from "
            "the SYNCTHING_ROOT environment variable. '~' and relative paths "
            "are expanded. This flag wins over the environment."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=VALID_LOG_LEVELS,
        help="Log level for this run. Overrides AGENT_LOG_LEVEL and the config file.",
    )
    return parser


def config_file_path(root: str | Path) -> Path:
    """Where the shared config file lives, given an **already resolved** root.

    The caller must resolve first. Deriving this from a raw ``~`` or relative
    root produces a path that does not exist, which reads as "no config file"
    rather than as a bug -- the silent-failure shape issue #93 D1 exists to
    eliminate.
    """
    return resolve_root(root) / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def _resolve_root(raw: str) -> Path:
    return resolve_root(raw)


def _cli_layer(args: argparse.Namespace) -> dict[str, Any]:
    """Only the flags the operator actually supplied.

    An unset optional flag must not enter the merge: ``None`` would overwrite
    a value from the environment or the shared file.
    """
    layer: dict[str, Any] = {}
    if args.node_id is not None:
        layer["NODE_ID"] = args.node_id
    if args.syncthing_root is not None:
        layer["SYNCTHING_ROOT"] = args.syncthing_root
    if args.log_level is not None:
        layer["LOG_LEVEL"] = args.log_level
    return layer


def _env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    layer: dict[str, Any] = {}
    for source_name, field_name in _SOURCE_NAMES.items():
        if source_name in environ:
            layer[field_name] = environ[source_name]
    return layer


def _toml_layer(path: Path) -> dict[str, Any]:
    """Read the shared config file. Absent is normal; malformed is an error."""
    if not path.is_file():
        return {}

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: cannot be read as TOML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a table of keys at the top level")

    unknown = sorted(set(raw) - set(_SOURCE_NAMES))
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) {', '.join(unknown)}. Valid keys are "
            f"{', '.join(sorted(_SOURCE_NAMES))}. This file replicates to every "
            "node, so a typo must fail on all of them rather than be ignored on "
            "all of them."
        )

    forbidden = sorted(set(raw) & _FORBIDDEN_TOML_KEYS)
    if "NODE_ID" in forbidden:
        raise ConfigError(
            f"{path}: NODE_ID must not appear in the shared config file. Every "
            "node reads the same replicated file, so a NODE_ID here would make "
            "every agent claim the same identity. Pass --node-id or set the "
            "NODE_ID environment variable on this machine instead."
        )
    if "SYNCTHING_ROOT" in forbidden:
        raise ConfigError(
            f"{path}: SYNCTHING_ROOT cannot be set in this file - it is what "
            "locates this file. Set the SYNCTHING_ROOT environment variable on "
            "this machine, or pass --syncthing-root."
        )

    layer: dict[str, Any] = {}
    for source_name, value in raw.items():
        if source_name in _FORBIDDEN_TOML_KEYS:
            continue
        layer[_SOURCE_NAMES[source_name]] = value
    return layer


def build_settings(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    toml_path: Path | None = None,
) -> AgentSettings:
    """Merge CLI, environment, config file and defaults into one settings object.

    Precedence, highest first: **CLI flag, environment variable, TOML file,
    field default**. The layers are merged here rather than delegated to
    pydantic-settings because the pinned 2.0.0 has no TOML source at all and
    passes init kwargs ahead of the environment -- handing it the file as init
    kwargs would make the file beat the environment with no error and no
    warning (issue #93 R1).

    Nothing is read from the process environment or the working directory
    implicitly: ``environ`` defaults to ``os.environ`` *at call time*, and the
    config path is derived from the resolved root, never searched for. That is
    what makes the lifecycle tests runnable with a 0.1 s shutdown grace instead
    of 30 s.
    """
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    env = os.environ if environ is None else environ
    from_cli = _cli_layer(args)

    raw_root = from_cli.get("SYNCTHING_ROOT") or env.get("SYNCTHING_ROOT")
    if not raw_root:
        raise ConfigError(
            "SYNCTHING_ROOT is required and has no default. Set the "
            "SYNCTHING_ROOT environment variable on this machine, or pass "
            "--syncthing-root. There is deliberately no fallback directory: an "
            "agent that silently syncs somewhere wrong is worse than one that "
            "refuses to start."
        )

    # Resolve first, derive second. The reverse order is issue #93 D1.
    root = _resolve_root(str(raw_root))
    path = config_file_path(root) if toml_path is None else Path(toml_path)

    merged = {**_toml_layer(path), **_env_layer(env), **from_cli}

    # The resolved root is authoritative: `raw_root` already applied CLI over
    # env, and the file cannot supply the key at all (NHD-3). Writing it back
    # keeps a `~` or relative spelling out of the model.
    merged["SYNCTHING_ROOT"] = root

    if "NODE_ID" not in merged:
        raise ConfigError(
            f"NODE_ID is required and has no default. Pass --node-id <id>, or "
            f"set the NODE_ID environment variable on this machine. It must not "
            f"come from {path}: every node reads that same replicated file, so "
            "a NODE_ID there would make every agent claim the same identity. A "
            "guessed node id impersonates a node."
        )

    if "AGENT_STATE_DIR" not in merged:
        merged["AGENT_STATE_DIR"] = root / STATE_DIR_NAME / str(merged["NODE_ID"])

    try:
        settings = AgentSettings(**merged)
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    # Deliberate, one-way: `shared/file_ops/path_utils.py` and everything built
    # on it read `os.environ["SYNCTHING_ROOT"]` directly and have no injection
    # point, which issue #98 needs. The value is published *from* the resolved
    # root and is never read back in as a precedence input (issue #93 VM-4).
    os.environ["SYNCTHING_ROOT"] = str(settings.SYNCTHING_ROOT)

    return settings
