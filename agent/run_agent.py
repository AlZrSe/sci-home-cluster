#!/usr/bin/env python3
"""
Worker agent entry point.

A shim, on purpose: parse, build settings, configure logging once, run. It owns
no handler of its own. The previous version installed a plain ``StreamHandler``
*before* configuring logging, which turned the later configuration into a
no-op and left the ``[node=...]`` field of every shipped log line empty
(issue #93 D2). There is exactly one logging entry point in this package, and
it is :func:`agent.logging_config.configure_logging`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import selectors
from pathlib import Path
from typing import Mapping, Sequence

from agent.config import (
    EXIT_CONFIG_ERROR,
    EXIT_INTERNAL_ERROR,
    EXIT_OK,
    AgentSettings,
    ConfigError,
    build_settings,
)
from agent.logging_config import configure_logging
from agent.loop import Agent

logger = logging.getLogger(__name__)

# Repository root anchored from this file's location (agent/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Agent's own directory (where run_agent.py or the frozen binary lives)
# This is the primary location for .env when deployed via Syncthing
AGENT_DIR = Path(__file__).resolve().parent

# Required variables that have no safe default
REQUIRED_VARS = frozenset({"NODE_ID", "SYNCTHING_ROOT"})


def _extract_syncthing_root_from_argv(argv: Sequence[str] | None) -> str | None:
    """Extract --syncthing-root value from argv before full parsing.

    Returns the value if found, None otherwise.
    """
    if argv is None:
        return None
    # Simple manual parse for --syncthing-root=value or --syncthing-root value
    for i, arg in enumerate(argv):
        if arg == "--syncthing-root":
            if i + 1 < len(argv):
                return argv[i + 1]
        elif arg.startswith("--syncthing-root="):
            return arg.split("=", 1)[1]
    return None


def _extract_node_id_from_argv(argv: Sequence[str] | None) -> str | None:
    """Extract --node-id value from argv before full parsing.

    Returns the value if found, None otherwise.
    """
    if argv is None:
        return None
    for i, arg in enumerate(argv):
        if arg == "--node-id":
            if i + 1 < len(argv):
                return argv[i + 1]
        elif arg.startswith("--node-id="):
            return arg.split("=", 1)[1]
    return None


def _copy_env_to_extraction_dir() -> bool:
    """Copy .env from deployment location to extraction directory for frozen builds.

    For frozen builds (PyInstaller), the binary runs from an extraction temp
    directory (sys._MEIPASS), but the .env file sits next to the binary in the
    Syncthing folder. This function copies .env from the deployment location
    (next to sys.executable) to the extraction directory so the agent can find it.

    Only copies .env (not .env.example). Returns True if copy succeeded or not
    needed, False on failure.
    """
    if not getattr(sys, "frozen", False):
        return True

    # Deployment location: where the binary lives (Syncthing folder)
    deployment_dir = Path(sys.executable).resolve().parent
    deployment_env = deployment_dir / ".env"

    # Extraction directory: where the bundle unpacks (sys._MEIPASS or __file__ parent)
    if hasattr(sys, "_MEIPASS") and sys._MEIPASS:
        extraction_dir = Path(sys._MEIPASS)
    else:
        # Fallback: parent of this file's location
        extraction_dir = Path(__file__).resolve().parent

    extraction_env = extraction_dir / ".env"

    # If .env already exists in extraction dir, nothing to do
    if extraction_env.exists():
        return True

    # Copy from deployment location if source exists
    try:
        if deployment_env.exists():
            shutil.copy2(deployment_env, extraction_env)
            sys.stderr.write(f"Copied .env from {deployment_env} to {extraction_env}\n")
            sys.stderr.flush()
        return True
    except Exception as exc:
        sys.stderr.write(f"Failed to copy .env to extraction dir: {exc}\n")
        sys.stderr.flush()
        return False


def _get_env_exists() -> bool:
    """Check if .env file exists (extraction dir > agent dir > repo root)."""
    agent_env_file = AGENT_DIR / ".env"
    repo_env_file = ENV_FILE

    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS") and sys._MEIPASS:
            extraction_env = Path(sys._MEIPASS) / ".env"
            if extraction_env.exists():
                return True
    return agent_env_file.exists() or repo_env_file.exists()


def _get_missing_required_vars(
    argv: Sequence[str] | None, env: Mapping[str, str]
) -> set[str]:
    """Determine which required vars are missing from CLI args and environment."""
    missing_required = set()
    for var in REQUIRED_VARS:
        cli_value = (
            _extract_syncthing_root_from_argv(argv) if var == "SYNCTHING_ROOT" else None
        )
        node_id_cli = None
        if var == "NODE_ID" and argv is not None:
            for i, arg in enumerate(argv):
                if arg == "--node-id" and i + 1 < len(argv):
                    node_id_cli = argv[i + 1]
                    break
                elif arg.startswith("--node-id="):
                    node_id_cli = arg.split("=", 1)[1]
                    break

        if var == "SYNCTHING_ROOT" and cli_value:
            continue
        if var == "NODE_ID" and node_id_cli:
            continue
        if var in env:
            continue
        missing_required.add(var)
    return missing_required


def _detect_first_run(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, set[str]]:
    """Detect if this is a first run (missing .env or missing required vars).

    Returns:
        A tuple of (is_first_run, missing_required_vars).
        - is_first_run: True if .env is missing OR required vars are not set
        - missing_required_vars: set of required var names that are not available
    """
    env = os.environ if environ is None else environ
    env_exists = _get_env_exists()
    missing_required = _get_missing_required_vars(argv, env)

    # First run if .env is missing OR required vars are missing
    is_first_run = not env_exists or bool(missing_required)
    return is_first_run, missing_required


def _prompt_create_env(timeout_seconds: float = 5.0) -> bool:
    """Prompt the operator to create .env.

    Returns True if the operator confirmed (y/Y), False otherwise
    (n/N/Enter/timeout/Ctrl+C).
    """
    prompt = (
        "No .env file found and required configuration is missing.\n"
        "Create one now? [y/N]: "
    )
    sys.stderr.write(prompt)
    sys.stderr.flush()

    try:
        # Use selectors for cross-platform timeout on stdin
        # On Windows, selectors works with sys.stdin
        sel = selectors.DefaultSelector()
        sel.register(sys.stdin, selectors.EVENT_READ)
        ready = sel.select(timeout_seconds)
        sel.unregister(sys.stdin)
        sel.close()

        if not ready:
            # Timeout - treat as decline
            sys.stderr.write("\n")
            sys.stderr.flush()
            return False

        # Read the response
        response = sys.stdin.readline().strip().lower()
        sys.stderr.write("\n")
        sys.stderr.flush()
        return response == "y"

    except KeyboardInterrupt:
        sys.stderr.write("\n")
        sys.stderr.flush()
        return False
    except Exception:
        # Any other error - treat as decline
        sys.stderr.write("\n")
        sys.stderr.flush()
        return False


def _run_first_run_flow(
    argv: Sequence[str], environ: Mapping[str, str] | None = None
) -> bool:
    """Run the first-run detection and prompt flow.

    Returns True if we should proceed to build_settings, False if we should exit.
    """
    is_first_run, missing_required = _detect_first_run(argv, environ)

    if not is_first_run:
        return True

    # Check if we're in an interactive TTY
    if sys.stdin.isatty():
        # Interactive - prompt to create .env
        confirmed = _prompt_create_env()
        if confirmed:
            # Create empty .env at agent directory (deployment location)
            try:
                target_env_file = AGENT_DIR / ".env"
                target_env_file.touch()
                sys.stderr.write(f"Created empty {target_env_file}\n")
                sys.stderr.flush()
                # Re-check after creation - will be handled by build_settings
                return True
            except Exception as exc:
                sys.stderr.write(f"Failed to create .env: {exc}\n")
                sys.stderr.flush()
                return False
        else:
            # Declined or timeout - proceed with defaults for optional vars
            # build_settings will be called with first_run_ok=True
            return True
    else:
        # Non-interactive - log warning and proceed
        sys.stderr.write(
            "WARNING: Running in non-interactive mode; skipping .env creation prompt. "
            "Set NODE_ID and SYNCTHING_ROOT via environment or CLI flags.\n"
        )
        sys.stderr.flush()
        return True


def _check_existing_lock(node_id: str, lock_file: Path, current_pid: int) -> None:
    """Check if an existing lock file indicates a running agent.

    Exits with error if another agent with the same node_id is running.
    """
    if not lock_file.exists():
        return

    try:
        content = lock_file.read_text().strip()
        if not content:
            return
        parts = content.split()
        locked_pid = int(parts[0]) if parts else 0
    except (ValueError, IndexError):
        return

    if locked_pid <= 0 or locked_pid == current_pid:
        return

    try:
        import psutil
    except ImportError:
        return

    if not psutil.pid_exists(locked_pid):
        return

    try:
        proc = psutil.Process(locked_pid)
        cmdline = proc.cmdline()
        if any("run_agent" in c or "run-agent" in c for c in cmdline):
            msg = (
                f"ERROR: Another agent is running with "
                f"node_id={node_id} (PID {locked_pid}). "
                f"Stop it first.\n"
            )
            sys.stderr.write(msg)
            sys.stderr.flush()
            sys.exit(1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def _check_single_agent_lock(node_id: str) -> None:
    """Ensure only one agent runs per node_id using a lock file.

    Creates a lock file at <SYNCTHING_ROOT>/.agent/<node_id>.lock with our PID.
    If lock exists and process is alive, exits with error.
    Lock file is removed on normal exit.
    """
    import atexit
    import signal
    import time

    # Get syncthing root from args or environment
    syncthing_root = _extract_syncthing_root_from_argv(sys.argv[1:]) or os.environ.get(
        "SYNCTHING_ROOT"
    )
    if not syncthing_root:
        return  # Can't check without syncthing_root

    lock_dir = Path(syncthing_root) / ".agent" / node_id
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "agent.lock"

    current_pid = os.getpid()

    # Check existing lock
    _check_existing_lock(node_id, lock_file, current_pid)

    # Write our lock
    lock_file.write_text(f"{current_pid} {time.time()}")

    # Register cleanup
    def cleanup_lock():
        try:
            if lock_file.exists():
                content = lock_file.read_text().strip()
                parts = content.split()
                if parts and int(parts[0]) == current_pid:
                    lock_file.unlink()
        except Exception:
            pass

    atexit.register(cleanup_lock)
    # Also handle signals
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda s, f: (cleanup_lock(), sys.exit(0)))
        except (OSError, ValueError):
            pass


def _warn_defaulted_fields(
    settings: "AgentSettings", defaulted_fields: set[str]
) -> None:
    """Emit consolidated WARNING for optional settings that fell back to defaults."""
    precedence_note = "Precedence: --flag > $ENV_VAR > nodes/agent.toml > default"
    warning_lines = [
        "WARNING: Using default configuration for the following settings "
        "(override via CLI, env, or nodes/agent.toml):"
    ]
    env_var_map: dict[str, tuple[str, str | None]] = {
        "LOG_LEVEL": ("AGENT_LOG_LEVEL", "--log-level"),
        "SHELL": ("AGENT_SHELL", "--shell"),
        "SHUTDOWN_GRACE_S": ("AGENT_SHUTDOWN_GRACE_S", None),
        "FOLDER_WATCH_INTERVAL_S": ("AGENT_FOLDER_WATCH_INTERVAL_S", None),
        "FOLDER_RETRY_MAX_S": ("AGENT_FOLDER_RETRY_MAX_S", None),
        "WATCHER_LIVENESS_INTERVAL_S": ("AGENT_WATCHER_LIVENESS_INTERVAL_S", None),
        "HEARTBEAT_INTERVAL_S": ("AGENT_HEARTBEAT_INTERVAL_S", None),
        "HEARTBEAT_JITTER_S": ("AGENT_HEARTBEAT_JITTER_S", None),
        "AGENT_STATE_DIR": ("AGENT_STATE_DIR", None),
    }

    for field in sorted(defaulted_fields):
        env_var, cli_flag = env_var_map.get(field, (f"AGENT_{field}", None))
        override_parts: list[str] = []
        if cli_flag:
            override_parts.append(cli_flag)
        override_parts.append(f"${env_var}")
        override_str = " or ".join(override_parts)
        val = getattr(settings, field)
        warning_lines.append(
            f"  {field}={val} (default; override with {override_str})"
        )
    warning_lines.append(precedence_note)
    logger.warning("\n".join(warning_lines))


def main(argv: Sequence[str] | None = None) -> int:
    """Return the process exit code. See ``agent.config`` for the table."""
    args = list(sys.argv[1:] if argv is None else argv)

    # For frozen builds, copy .env from deployment location to extraction dir
    if not _copy_env_to_extraction_dir():
        return EXIT_CONFIG_ERROR

    # Run first-run detection and prompt before building settings
    if not _run_first_run_flow(args):
        return EXIT_CONFIG_ERROR

    # Determine if we're in first-run mode (for allowing defaults on optional vars)
    is_first_run, _ = _detect_first_run(args)

    # Get node_id early to check for other agent copies with the same node_id
    node_id = _extract_node_id_from_argv(args)
    if not node_id:
        node_id = os.environ.get("NODE_ID")
    if node_id:
        _check_single_agent_lock(node_id)

    try:
        settings, defaulted_fields = build_settings(args, first_run_ok=is_first_run)
    except ConfigError as exc:
        # Nothing is configured yet, so there is no logger to say this with.
        # argparse reports its own errors the same way.
        sys.stderr.write(f"agent configuration error: {exc}\n")
        return EXIT_CONFIG_ERROR
    except SystemExit as exc:
        # argparse rejected the command line and has already written its usage
        # message. Letting it through would make a bad flag exit 2 while every
        # other bad input exits EXIT_CONFIG_ERROR, and would contradict this
        # function's docstring -- and `sys.exit(main())` would propagate the
        # exception instead of returning the documented code (issue #93 QA D-G).
        # `--help` exits 0 and must stay 0.
        return EXIT_OK if exc.code in (0, None) else EXIT_CONFIG_ERROR

    configure_logging(settings.LOG_LEVEL, settings.NODE_ID)

    if defaulted_fields:
        _warn_defaulted_fields(settings, defaulted_fields)

    try:
        return asyncio.run(Agent(settings).run())
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception:
        logger.exception("unhandled internal error")
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
