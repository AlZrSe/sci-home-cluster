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

# Required variables that have no safe default
REQUIRED_VARS = frozenset({"NODE_ID", "SYNCTHING_ROOT"})


def _detect_first_run(
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, set[str]]:
    """Detect if this is a first run (missing .env or missing required vars).

    Returns:
        A tuple of (is_first_run, missing_required_vars).
        - is_first_run: True if .env is missing OR required vars are not set
        - missing_required_vars: set of required var names that are not available
    """
    env = os.environ if environ is None else environ

    # Check if .env file exists at repo root
    env_exists = ENV_FILE.exists()

    # Check which required vars are available from any source (CLI > env > .env)
    # We check env here because CLI args are parsed later in build_settings
    missing_required = set()
    for var in REQUIRED_VARS:
        if var not in env:
            missing_required.add(var)

    # First run if .env is missing OR required vars are missing
    is_first_run = not env_exists or bool(missing_required)
    return is_first_run, missing_required


def _prompt_create_env(timeout_seconds: float = 5.0) -> bool:
    """Prompt the operator to create .env from .env.example.

    Returns True if the operator confirmed (y/Y), False otherwise
    (n/N/Enter/timeout/Ctrl+C).
    """
    if not ENV_EXAMPLE.exists():
        sys.stderr.write(
            f"error: {ENV_EXAMPLE} not found; run from a complete checkout.\n"
        )
        return False

    prompt = (
        f"No .env file found at {ENV_FILE} and required configuration is missing.\n"
        f"Create one now from .env.example? [y/N]: "
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
    is_first_run, missing_required = _detect_first_run(environ)

    if not is_first_run:
        return True

    # Check if we're in an interactive TTY
    if sys.stdin.isatty():
        # Interactive - prompt to create .env
        confirmed = _prompt_create_env()
        if confirmed:
            # Copy .env.example to .env
            try:
                shutil.copy2(ENV_EXAMPLE, ENV_FILE)
                sys.stderr.write(f"Created {ENV_FILE} from {ENV_EXAMPLE}\n")
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


def main(argv: Sequence[str] | None = None) -> int:
    """Return the process exit code. See ``agent.config`` for the table."""
    args = list(sys.argv[1:] if argv is None else argv)

    # Run first-run detection and prompt before building settings
    if not _run_first_run_flow(args):
        return EXIT_CONFIG_ERROR

    # Determine if we're in first-run mode (for allowing defaults on optional vars)
    is_first_run, _ = _detect_first_run()

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

    # Emit consolidated WARNING for any optional settings that fell back to defaults
    if defaulted_fields:
        # Build the warning message with precedence order
        precedence_note = "Precedence: --flag > $ENV_VAR > nodes/agent.toml > default"
        warning_lines = [
            "WARNING: Using default configuration for the following settings "
            "(override via CLI, env, or nodes/agent.toml):"
        ]
        # Map field name to env var and CLI flag
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

    try:
        return asyncio.run(Agent(settings).run())
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception:
        logger.exception("unhandled internal error")
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
