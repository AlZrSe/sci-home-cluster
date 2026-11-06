"""Unit tests for SHARED_TOKEN generation (issue #64)."""

from __future__ import annotations

import logging

import pytest

from backend.core.config import _resolve_shared_token

pytestmark = pytest.mark.unit


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("SHARED_TOKEN", raising=False)
    return tmp_path


def test_generated_shared_token_persisted_and_survives_restart(
    state_dir, monkeypatch, caplog
):
    """AC-1/AC-2: a generated token is persisted and read back silently."""
    monkeypatch.delenv("SHARED_TOKEN", raising=False)

    with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
        first = _resolve_shared_token(shc_state_dir=str(state_dir))

    assert first
    token_file = state_dir / "shared_token"
    assert token_file.exists()
    assert token_file.read_text(encoding="utf-8").strip() == first

    # Restart: read back silently.
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
        second = _resolve_shared_token(shc_state_dir=str(state_dir))

    assert second == first
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


def test_generation_logs_warning_with_path_never_value(state_dir, monkeypatch, caplog):
    """AC-4: generation logs a WARNING naming the path, never the value."""
    monkeypatch.delenv("SHARED_TOKEN", raising=False)

    with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
        generated = _resolve_shared_token(shc_state_dir=str(state_dir))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    message = warnings[0].getMessage()
    assert str(state_dir / "shared_token") in message

    for record in caplog.records:
        text = record.getMessage()
        assert generated not in text
        for start in range(len(generated) - 8 + 1):
            prefix = generated[start : start + 8]
            assert prefix not in text, f"leaked {prefix!r}"
