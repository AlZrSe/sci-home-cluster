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
import sys
from typing import Sequence

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


def main(argv: Sequence[str] | None = None) -> int:
    """Return the process exit code. See ``agent.config`` for the table."""
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        settings = build_settings(args)
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

    try:
        return asyncio.run(Agent(settings).run())
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception:
        logger.exception("unhandled internal error")
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
