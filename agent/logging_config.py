"""
Logging setup owned by the worker agent.

Issue #68 wants one logging scheme across entrypoints but is scoped to the
backend, so this module establishes the contract instead: *each entrypoint owns
a ``configure_logging(level, context)`` function*. Zero backend edits.

Two mechanisms stamp the node id on records, because one of them alone has a
hole:

* a **record factory**, which is process-global and therefore covers records
  emitted by ``agent.*``, ``shared.*``, ``backend.*`` and any third-party
  logger; and
* a **``NodeIdFilter`` on the installed handler**, which is the backup for when
  a library calls ``logging.setLogRecordFactory`` and silently replaces ours.

A filter on the *root logger* would not work: ``Logger.callHandlers`` walks the
handler chain and never invokes a ``Logger.filter``, which only runs on the
logger that emitted the record (issue #93 K8).
"""

from __future__ import annotations

import logging
from typing import Any, Final, TextIO, cast

#: Not a settings field. The format knob is deferred until issue #68 picks one
#: (issue #93 D2: a format the shipped process does not actually render is not
#: a delivered promise, so this constant and its only installer must be the
#: same object).
LOG_FORMAT: Final[str] = (
    "%(asctime)s %(levelname)s [node=%(node_id)s] %(name)s: %(message)s"
)

#: Levels ``logging`` itself defines, plus the ``WARN``/``FATAL`` aliases.
#:
#: The aliases are deprecated by the stdlib but common in hand-written operator
#: configuration, and ``AGENT_LOG_LEVEL=WARN`` failing at startup is a footgun
#: worth two strings. They resolve through ``getattr(logging, ...)`` to the same
#: numeric levels as their canonical forms, so accepting them costs no ambiguity.
#: Resolving that way is also what turns a typo into a message naming the legal
#: values instead of an ``AttributeError`` at startup (issue #93 C10).
VALID_LOG_LEVELS: Final[tuple[str, ...]] = (
    "CRITICAL",
    "FATAL",
    "ERROR",
    "WARNING",
    "WARN",
    "INFO",
    "DEBUG",
    "NOTSET",
)

#: Rendered when a record reaches the formatter without a node id. Using the
#: stdlib's ``Formatter(defaults=...)`` rather than ``getattr(record, ...)`` at
#: format time keeps the fallback on the rendering path, where it is needed,
#: instead of on a class that nothing installs (issue #93 D3).
UNBOUND_NODE_ID: Final[str] = "<unbound>"

#: Marks handlers and record factories this module installed, so a second
#: ``configure_logging`` call replaces them instead of stacking on top. Without
#: this the module would grow a second handler per call and ``basicConfig``
#: would become a no-op again (issue #93 D2/C11).
_OWNED_HANDLER_ATTR: Final[str] = "_shc_agent_owned_handler"
_OWNED_FACTORY_ATTR: Final[str] = "_shc_agent_owned_factory"


class NodeIdFilter(logging.Filter):
    """Stamp ``node_id`` onto every record that reaches the handler."""

    def __init__(self, node_id: str) -> None:
        super().__init__()
        self.node_id = node_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.node_id = self.node_id  # type: ignore[attr-defined]
        return True


def resolve_level(level: str) -> int:
    """Turn a level name into its numeric level, or explain what is legal."""
    resolved = getattr(logging, str(level).upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(
            f"unknown log level {level!r}; valid levels are "
            f"{', '.join(VALID_LOG_LEVELS)}"
        )
    return resolved


def _install_record_factory(node_id: str) -> None:
    current = logging.getLogRecordFactory()
    if getattr(current, _OWNED_FACTORY_ATTR, False):
        base: Any = current.__dict__.get("_shc_agent_base_factory")
    else:
        base = current

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = cast(logging.LogRecord, base(*args, **kwargs))
        record.node_id = node_id  # type: ignore[attr-defined]
        return record

    setattr(factory, _OWNED_FACTORY_ATTR, True)
    setattr(factory, "_shc_agent_base_factory", base)
    logging.setLogRecordFactory(factory)


def configure_logging(
    level: str, node_id: str, *, stream: TextIO | None = None
) -> None:
    """Install the agent's handler on the root logger.

    ``logging.basicConfig`` is a no-op whenever the root logger already has a
    handler -- which is true under pytest and in any process that configured
    logging first -- so the level is set explicitly and the handler is installed
    unconditionally (issue #93 C11). Callers must not install a handler before
    calling this: that is exactly how the rendered line lost its node id in the
    lost implementation (issue #93 D2).
    """
    resolved = resolve_level(level)

    root = logging.getLogger()
    root.setLevel(resolved)

    _install_record_factory(node_id)

    for handler in list(root.handlers):
        if getattr(handler, _OWNED_HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(stream)
    handler.setLevel(resolved)
    handler.setFormatter(
        logging.Formatter(LOG_FORMAT, defaults={"node_id": UNBOUND_NODE_ID})
    )
    handler.addFilter(NodeIdFilter(node_id))
    setattr(handler, _OWNED_HANDLER_ATTR, True)
    root.addHandler(handler)
