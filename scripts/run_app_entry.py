"""Frozen-build entry point for the Scientific Home Cluster application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))


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


def main(argv: list[str] | None = None) -> int:
    _apply_frozen_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = parser.parse_args(argv)

    # Settings reads LOG_LEVEL from the environment at import time.
    os.environ.setdefault("LOG_LEVEL", args.log_level.upper())

    import uvicorn

    uvicorn.run(
        "backend.main:app",
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
