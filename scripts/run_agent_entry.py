"""Frozen-build entry point for the worker agent.

PyInstaller executes this file as ``__main__`` from inside the bundle, so its
own directory (``scripts/``) is on ``sys.path`` but the bundle root is not.
Insert the bundle root first, then delegate to :func:`agent.run_agent.main`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))

from agent.run_agent import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
