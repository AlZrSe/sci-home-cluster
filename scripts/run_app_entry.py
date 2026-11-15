"""Frozen-build entry point for the Scientific Home Cluster application."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import selectors
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))

# Repository root anchored from this file's location (scripts/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Required variables for the backend that have no safe default
REQUIRED_BACKEND_VARS = frozenset({"SYNCTHING_ROOT"})


def _apply_frozen_defaults() -> None:
    """Point writable state and the frontend at sane locations inside a frozen build.

    Must run before backend.core.config is imported, because Settings is
    constructed at import time and reads these env vars then.
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", _BUNDLE_ROOT))
    app_home = Path(sys.executable).resolve().parent
    os.environ.setdefault("SERVE_FRONTEND", "true")
    os.environ.setdefault("FRONTEND_DIR", str(meipass / "frontend"))
    os.environ.setdefault("SHC_STATE_DIR", str(app_home / ".shc"))
    os.environ.setdefault("SHC_DATA_DIR", str(app_home / "data"))


def _detect_first_run_backend(
    environ: dict[str, str] | None = None,
) -> tuple[bool, set[str]]:
    """Detect if this is a first run for the backend.

    Returns:
        A tuple of (is_first_run, missing_required_vars).
    """
    env = os.environ if environ is None else environ

    env_exists = ENV_FILE.exists()

    missing_required = set()
    for var in REQUIRED_BACKEND_VARS:
        if var not in env:
            missing_required.add(var)

    is_first_run = not env_exists or bool(missing_required)
    return is_first_run, missing_required


def _prompt_create_env() -> bool:
    """Prompt the operator to create .env from .env.example.

    Returns True if the operator confirmed (y/Y), False otherwise.
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
        sel = selectors.DefaultSelector()
        sel.register(sys.stdin, selectors.EVENT_READ)
        ready = sel.select(5.0)  # 5 second timeout
        sel.unregister(sys.stdin)
        sel.close()

        if not ready:
            sys.stderr.write("\n")
            sys.stderr.flush()
            return False

        response = sys.stdin.readline().strip().lower()
        sys.stderr.write("\n")
        sys.stderr.flush()
        return response == "y"

    except KeyboardInterrupt:
        sys.stderr.write("\n")
        sys.stderr.flush()
        return False
    except Exception:
        sys.stderr.write("\n")
        sys.stderr.flush()
        return False


def _run_first_run_flow_backend(argv: list[str] | None = None) -> bool:
    """Run the first-run detection and prompt flow for the backend.

    Returns True if we should proceed, False if we should exit.
    """
    is_first_run, missing_required = _detect_first_run_backend()

    if not is_first_run:
        return True

    if sys.stdin.isatty():
        confirmed = _prompt_create_env()
        if confirmed:
            try:
                shutil.copy2(ENV_EXAMPLE, ENV_FILE)
                sys.stderr.write(f"Created {ENV_FILE} from {ENV_EXAMPLE}\n")
                sys.stderr.flush()
                return True
            except Exception as exc:
                sys.stderr.write(f"Failed to create .env: {exc}\n")
                sys.stderr.flush()
                return False
        else:
            # Declined or timeout - proceed, backend will handle missing vars
            return True
    else:
        sys.stderr.write(
            "WARNING: Running in non-interactive mode; skipping .env creation prompt. "
            "Set SYNCTHING_ROOT via environment or .env file.\n"
        )
        sys.stderr.flush()
        return True


def main(argv: list[str] | None = None) -> int:
    # Run first-run detection and prompt before applying frozen defaults
    if not _run_first_run_flow_backend(argv):
        return 1

    _apply_frozen_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = parser.parse_args(argv)

    # Settings reads LOG_LEVEL from the environment at import time.
    os.environ.setdefault("LOG_LEVEL", args.log_level.upper())

    import uvicorn

    from backend.main import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1)
