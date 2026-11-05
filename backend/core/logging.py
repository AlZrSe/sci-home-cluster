"""Shared logging setup for every backend entrypoint (issue #68).

Only ``backend/main.py`` configured logging before this module existed, so
``alembic upgrade head``, the CLI and any subprocess saw no INFO records.
Centralising the call here gives every entrypoint the same behaviour without
duplicating the level resolution.
"""

from __future__ import annotations

import logging
from typing import Optional


def configure_logging(level: Optional[str] = None) -> None:
    """Install a root handler if one is missing and set the root level.

    ``level`` may be a level name (``"INFO"``) or ``None``, in which case the
    level is read from ``backend.core.config.settings.LOG_LEVEL`` when it is
    available and falls back to ``INFO``. Calling this more than once is safe:
    ``logging.basicConfig`` is a no-op once a handler is installed, and the
    level is always applied via ``setLevel``.
    """
    if level is None:
        try:
            from backend.core.config import settings

            level = settings.LOG_LEVEL
        except Exception:  # pragma: no cover - config not importable yet
            level = "INFO"

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(level=numeric)
    logging.getLogger().setLevel(numeric)
