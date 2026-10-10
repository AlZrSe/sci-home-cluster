"""Logging setup: the node id must reach the *rendered* line, not just the record.

The lost implementation satisfied "every record carries node_id" and shipped
log lines with no ``[node=...]`` at all, because a bootstrap step installed a
plain handler first and turned the later ``basicConfig`` into a no-op
(issue #93 D2). AC-5 is therefore worded around what an operator reads.
"""

from __future__ import annotations

import ast
import io
import logging
from pathlib import Path
from typing import Any

import pytest

from agent.logging_config import (
    LOG_FORMAT,
    UNBOUND_NODE_ID,
    VALID_LOG_LEVELS,
    NodeIdFilter,
    configure_logging,
)

pytestmark = pytest.mark.unit

NODE_ID = "node-rendered"


@pytest.fixture
def rendered(preserve_root_logging: None) -> io.StringIO:
    """A clean root logger whose only handler is the one under test."""
    stream = io.StringIO()
    logging.getLogger().handlers = []
    configure_logging("INFO", NODE_ID, stream=stream)
    return stream


def test_plain_handler_installed_first_does_not_hide_the_node_id(
    preserve_root_logging: None,
) -> None:
    """D2, reproduced exactly.

    The lost implementation installed a plain ``StreamHandler`` before
    configuring logging, which made the later ``basicConfig`` a documented no-op
    and left ``[node=...]`` out of every shipped line while the records still
    carried the attribute. The ``rendered`` fixture starts from a *clean* root,
    so it cannot see that; this one starts from a dirty one.
    """
    stream = io.StringIO()
    root = logging.getLogger()
    root.handlers = [
        logging.StreamHandler()  # the bootstrap handler, with no format
    ]
    configure_logging("INFO", NODE_ID, stream=stream)

    logging.getLogger("agent.loop").info("a line an operator would read")

    assert f"[node={NODE_ID}]" in stream.getvalue()


def test_every_record_carries_node_id(rendered: io.StringIO) -> None:
    logging.getLogger("agent.loop").info("from the agent")
    logging.getLogger("shared.file_ops.yaml_utils").info("from shared")
    record = logging.getLogRecordFactory()(
        "probe", logging.INFO, __file__, 1, "probe", None, None
    )
    assert getattr(record, "node_id", None) == NODE_ID


def test_rendered_line_contains_the_node_id(rendered: io.StringIO) -> None:
    """The operator-facing half of US-2, and the regression test for D2."""
    logging.getLogger("agent.loop").info("a line an operator would read")
    line = rendered.getvalue()
    assert f"[node={NODE_ID}]" in line
    assert "a line an operator would read" in line
    assert "INFO" in line


def test_unrelated_third_party_logger_carries_node_id(rendered: io.StringIO) -> None:
    logging.getLogger("some.third.party.library").info("hello from elsewhere")
    assert f"[node={NODE_ID}]" in rendered.getvalue()


def test_handler_filter_stamps_a_record_with_a_replaced_factory(
    rendered: io.StringIO,
) -> None:
    """The backup path for when a library replaces the record factory."""

    def bare(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return logging.LogRecord(*args, **kwargs)

    logging.setLogRecordFactory(bare)
    logging.getLogger("agent.loop").info("after the factory was swapped out")
    assert f"[node={NODE_ID}]" in rendered.getvalue()


def test_configure_logging_honours_level(preserve_root_logging: None) -> None:
    stream = io.StringIO()
    configure_logging("WARNING", NODE_ID, stream=stream)
    logging.getLogger("agent.loop").info("suppressed")
    logging.getLogger("agent.loop").warning("kept")
    output = stream.getvalue()
    assert "suppressed" not in output
    assert "kept" in output
    assert logging.getLogger().level == logging.WARNING


def test_unknown_log_level_is_rejected_listing_valid_levels(
    preserve_root_logging: None,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        configure_logging("LOUD", NODE_ID, stream=io.StringIO())
    message = str(excinfo.value)
    assert "LOUD" in message
    for level in VALID_LOG_LEVELS:
        assert level in message


def test_configure_logging_is_idempotent(preserve_root_logging: None) -> None:
    stream = io.StringIO()
    for _ in range(3):
        configure_logging("INFO", NODE_ID, stream=stream)

    owned = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_shc_agent_owned_handler", False)
    ]
    assert len(owned) == 1

    logging.getLogger("agent.loop").info("only once")
    assert stream.getvalue().count("only once") == 1


def test_record_without_node_id_does_not_raise(rendered: io.StringIO) -> None:
    """A record that reaches the formatter bare must render, not crash."""
    logging.setLogRecordFactory(
        lambda *args, **kwargs: logging.LogRecord(*args, **kwargs)
    )
    handler = logging.getLogger().handlers[0]
    handler.filters.clear()
    logging.getLogger("agent.loop").info("bare record")
    assert UNBOUND_NODE_ID in rendered.getvalue()


def test_node_id_filter_replaces_previous_node_id() -> None:
    record = logging.LogRecord("probe", logging.INFO, __file__, 1, "m", None, None)
    record.node_id = "stale"  # type: ignore[attr-defined]
    assert NodeIdFilter("fresh").filter(record) is True
    assert record.node_id == "fresh"  # type: ignore[attr-defined]


def test_every_root_handler_renders_the_node_id(preserve_root_logging: None) -> None:
    """The shipped process has exactly this handler and no other."""
    logging.getLogger().handlers = []
    configure_logging("INFO", NODE_ID, stream=io.StringIO())
    for handler in logging.getLogger().handlers:
        assert handler.formatter is not None
        assert handler.formatter._fmt == LOG_FORMAT
        assert any(
            isinstance(f, NodeIdFilter) and f.node_id == NODE_ID
            for f in handler.filters
        )


def test_log_format_carries_the_node_field() -> None:
    assert "node_id" in LOG_FORMAT


def test_no_unwired_class_is_shipped() -> None:
    """D3: a class whose reason for existing is not true is not carried.

    The lost implementation exported ``NodeIdFormatter``: defined, never
    installed, and therefore a lie about how the format is applied.
    """
    source = (Path(__file__).resolve().parents[1] / "logging_config.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert classes, "no classes found; the AST check is looking at the wrong file"
    for name in classes:
        assert (
            source.count(name) >= 2
        ), f"{name} is defined in logging_config and never used there"


def test_configure_logging_accepts_every_legal_level(
    preserve_root_logging: None,
) -> None:
    for level in VALID_LOG_LEVELS:
        configure_logging(level, NODE_ID, stream=io.StringIO())
