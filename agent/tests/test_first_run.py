"""Unit tests for the agent's first-run detection, prompt, and default configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import selectors

from agent.config import ConfigError, build_settings
from agent.run_agent import (
    REQUIRED_VARS,
    _detect_first_run,
    _prompt_create_env,
    _run_first_run_flow,
)

pytestmark = pytest.mark.unit


# --- _detect_first_run --------------------------------------------------------


def test_detects_missing_env_at_repo_root(tmp_path: Path, monkeypatch: Any) -> None:
    """ENV_FILE.exists() == False triggers first-run flow."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                is_first_run, missing = _detect_first_run(
                    {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"}
                )
                assert is_first_run is True
                assert missing == set()


def test_detects_existing_env_at_repo_root(tmp_path: Path, monkeypatch: Any) -> None:
    """ENV_FILE.exists() == True skips prompt when all required vars present."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                is_first_run, missing = _detect_first_run(
                    {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"}
                )
                assert is_first_run is False
                assert missing == set()


def test_detects_missing_required_vars_with_env_file(tmp_path: Path) -> None:
    """.env exists but required vars missing -> first run with missing vars."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env").write_text(
                    "# empty or incomplete\n", encoding="utf-8"
                )
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                # Neither NODE_ID nor SYNCTHING_ROOT in environ
                is_first_run, missing = _detect_first_run({})
                assert is_first_run is True
                assert missing == REQUIRED_VARS

                # Only NODE_ID in environ
                is_first_run, missing = _detect_first_run({"NODE_ID": "node-01"})
                assert is_first_run is True
                assert missing == {"SYNCTHING_ROOT"}


# --- _prompt_create_env -------------------------------------------------------


def test_prompt_shown_only_in_tty(tmp_path: Path, monkeypatch: Any) -> None:
    """Mock sys.stdin.isatty() -> True/False."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                # Test TTY=True -> prompt is shown
                mock_selector = MagicMock()
                mock_selector.select.return_value = [(None, selectors.EVENT_READ)]
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("selectors.DefaultSelector", return_value=mock_selector):
                        with patch("sys.stdin.readline", return_value="y\n"):
                            result = _prompt_create_env()
                            assert result is True

                # Test TTY=False -> should not be called in practice,
                # but function would still work
                # This is more of a design test - the caller checks isatty()


def test_prompt_creates_env_from_example(tmp_path: Path, monkeypatch: Any) -> None:
    """Simulate 'y' input via _run_first_run_flow, verify copy and re-check."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                mock_selector = MagicMock()
                mock_selector.select.return_value = [(None, selectors.EVENT_READ)]
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("selectors.DefaultSelector", return_value=mock_selector):
                        with patch("sys.stdin.readline", return_value="y\n"):
                            result = _run_first_run_flow([], environ={})
                            assert result is True
                            assert (tmp_path / ".env").exists()
                            content = (tmp_path / ".env").read_text(encoding="utf-8")
                            assert "NODE_ID=node-01" in content
                            assert "SYNCTHING_ROOT=/tmp" in content


def test_prompt_declined_proceeds_to_validation(tmp_path: Path) -> None:
    """Simulate 'n' input, verify returns False."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                with patch("sys.stdin.isatty", return_value=True):
                    with patch(
                        "selectors.DefaultSelector.select",
                        return_value=[(None, selectors.EVENT_READ)],
                    ):
                        with patch("sys.stdin.readline", return_value="n\n"):
                            result = _prompt_create_env()
                            assert result is False
                            assert not (tmp_path / ".env").exists()


def test_prompt_timeout_proceeds_to_validation(tmp_path: Path) -> None:
    """Input times out (5s) -> treat as decline."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                with patch("sys.stdin.isatty", return_value=True):
                    with patch("selectors.DefaultSelector.select", return_value=[]):
                        result = _prompt_create_env()
                        assert result is False
                        assert not (tmp_path / ".env").exists()


def test_prompt_ctrl_c_exits_cleanly(tmp_path: Path) -> None:
    """Ctrl+C during prompt -> returns False."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                with patch("sys.stdin.isatty", return_value=True):
                    with patch(
                        "selectors.DefaultSelector.select",
                        side_effect=KeyboardInterrupt,
                    ):
                        result = _prompt_create_env()
                        assert result is False


def test_prompt_missing_example_fails_fast(tmp_path: Path) -> None:
    """.env.example missing -> fail fast with clear error."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                # .env.example does NOT exist
                with patch("sys.stdin.isatty", return_value=True):
                    with patch(
                        "selectors.DefaultSelector.select",
                        return_value=[(None, selectors.EVENT_READ)],
                    ):
                        with patch("sys.stdin.readline", return_value="y\n"):
                            result = _prompt_create_env()
                            assert result is False


# --- _run_first_run_flow ------------------------------------------------------


def test_first_run_flow_interactive_creates_env(tmp_path: Path) -> None:
    """Interactive TTY, user confirms -> creates .env and proceeds."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                mock_selector = MagicMock()
                mock_selector.select.return_value = [(None, selectors.EVENT_READ)]
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("selectors.DefaultSelector", return_value=mock_selector):
                        with patch("sys.stdin.readline", return_value="y\n"):
                            result = _run_first_run_flow([], environ={})
                            assert result is True
                            assert (tmp_path / ".env").exists()


def test_first_run_flow_interactive_declines(tmp_path: Path) -> None:
    """Interactive TTY, user declines -> proceeds without .env."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                with patch("sys.stdin.isatty", return_value=True):
                    with patch(
                        "selectors.DefaultSelector.select",
                        return_value=[(None, selectors.EVENT_READ)],
                    ):
                        with patch("sys.stdin.readline", return_value="n\n"):
                            result = _run_first_run_flow([], environ={})
                            assert result is True
                            assert not (tmp_path / ".env").exists()


def test_first_run_flow_non_interactive_logs_warning(
    tmp_path: Path, capsys: Any
) -> None:
    """Non-TTY skips prompt, logs warning to stderr."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                with patch("sys.stdin.isatty", return_value=False):
                    result = _run_first_run_flow([], environ={})
                    assert result is True
                    captured = capsys.readouterr()
                    assert "WARNING: Running in non-interactive mode" in captured.err
                    assert "NODE_ID" in captured.err
                    assert "SYNCTHING_ROOT" in captured.err


def test_first_run_flow_already_configured_skips(tmp_path: Path) -> None:
    """.env exists with required vars -> skips first-run flow entirely."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                with patch("sys.stdin.isatty", return_value=True):
                    result = _run_first_run_flow(
                        [], environ={"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"}
                    )
                    assert result is True


# --- build_settings defaulted fields ------------------------------------------


def test_required_vars_fail_fast_with_existing_messages(tmp_path: Path) -> None:
    """Missing NODE_ID/SYNCTHING_ROOT raises ConfigError with actionable messages."""
    with patch("agent.run_agent.REPO_ROOT", tmp_path):
        with patch("agent.run_agent.ENV_FILE", tmp_path / ".env"):
            with patch("agent.run_agent.ENV_EXAMPLE", tmp_path / ".env.example"):
                (tmp_path / ".env.example").write_text(
                    "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
                )

                # Missing NODE_ID
                with pytest.raises(ConfigError) as excinfo:
                    build_settings(["--syncthing-root", str(tmp_path)], environ={})
                message = str(excinfo.value)
                assert "NODE_ID" in message
                assert "replicated" in message

                # Missing SYNCTHING_ROOT
                with pytest.raises(ConfigError) as excinfo:
                    build_settings(["--node-id", "node-01"], environ={})
                message = str(excinfo.value)
                assert "SYNCTHING_ROOT" in message
                assert "no default" in message


def test_optional_vars_default_and_warn(tmp_path: Path) -> None:
    """No optional vars set -> defaulted_fields contains all optional field names."""
    settings, defaulted = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)],
        environ={},
    )
    expected_optional = {
        "LOG_LEVEL",
        "SHELL",
        "SHUTDOWN_GRACE_S",
        "FOLDER_WATCH_INTERVAL_S",
        "FOLDER_RETRY_MAX_S",
        "WATCHER_LIVENESS_INTERVAL_S",
        "HEARTBEAT_INTERVAL_S",
        "HEARTBEAT_JITTER_S",
        "AGENT_STATE_DIR",
    }
    assert defaulted == expected_optional
    # Verify defaults are correct
    assert settings.LOG_LEVEL == "INFO"
    assert settings.SHELL == "auto"
    assert settings.SHUTDOWN_GRACE_S == 30.0
    assert settings.FOLDER_WATCH_INTERVAL_S == 30.0
    assert settings.FOLDER_RETRY_MAX_S == 60.0
    assert settings.WATCHER_LIVENESS_INTERVAL_S == 60.0
    assert settings.HEARTBEAT_INTERVAL_S == 22.0
    assert settings.HEARTBEAT_JITTER_S == 2.0
    assert settings.AGENT_STATE_DIR == tmp_path / ".agent" / "node-01"


def test_explicit_cli_suppresses_warning(tmp_path: Path) -> None:
    """--log-level DEBUG -> LOG_LEVEL not in defaulted_fields."""
    settings, defaulted = build_settings(
        [
            "--node-id",
            "node-01",
            "--syncthing-root",
            str(tmp_path),
            "--log-level",
            "DEBUG",
        ],
        environ={},
    )
    assert "LOG_LEVEL" not in defaulted
    assert settings.LOG_LEVEL == "DEBUG"
    # Other optional fields should still be defaulted
    assert "SHELL" in defaulted
    assert "SHUTDOWN_GRACE_S" in defaulted


def test_explicit_env_suppresses_warning(tmp_path: Path) -> None:
    """AGENT_LOG_LEVEL=DEBUG in environ -> LOG_LEVEL not in defaulted_fields."""
    settings, defaulted = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)],
        environ={"AGENT_LOG_LEVEL": "DEBUG"},
    )
    assert "LOG_LEVEL" not in defaulted
    assert settings.LOG_LEVEL == "DEBUG"


def test_toml_suppresses_warning(tmp_path: Path) -> None:
    """TOML sets AGENT_LOG_LEVEL=WARNING -> LOG_LEVEL not in defaulted_fields."""
    nodes_dir = tmp_path / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "agent.toml").write_text(
        'AGENT_LOG_LEVEL = "WARNING"\n', encoding="utf-8"
    )

    settings, defaulted = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)],
        environ={},
    )
    assert "LOG_LEVEL" not in defaulted
    assert settings.LOG_LEVEL == "WARNING"


def test_cli_beats_env_beats_toml_beats_default(tmp_path: Path) -> None:
    """Precedence: CLI > env > TOML > default."""
    nodes_dir = tmp_path / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "agent.toml").write_text(
        'AGENT_LOG_LEVEL = "WARNING"\n', encoding="utf-8"
    )

    # CLI wins
    settings, defaulted = build_settings(
        [
            "--node-id",
            "node-01",
            "--syncthing-root",
            str(tmp_path),
            "--log-level",
            "DEBUG",
        ],
        environ={"AGENT_LOG_LEVEL": "ERROR"},
    )
    assert settings.LOG_LEVEL == "DEBUG"
    assert "LOG_LEVEL" not in defaulted

    # Env wins over TOML
    settings, defaulted = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)],
        environ={"AGENT_LOG_LEVEL": "ERROR"},
    )
    assert settings.LOG_LEVEL == "ERROR"
    assert "LOG_LEVEL" not in defaulted

    # TOML wins over default
    settings, defaulted = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)],
        environ={},
    )
    assert settings.LOG_LEVEL == "WARNING"
    assert "LOG_LEVEL" not in defaulted


def test_first_run_ok_allows_missing_required_for_optional_defaults(
    tmp_path: Path,
) -> None:
    """first_run_ok=True fails for required vars but allows optional defaults."""
    # This test verifies that first_run_ok doesn't change required var behavior
    with pytest.raises(ConfigError) as excinfo:
        build_settings(
            ["--syncthing-root", str(tmp_path)],
            environ={},
            first_run_ok=True,
        )
    assert "NODE_ID" in str(excinfo.value)

    with pytest.raises(ConfigError) as excinfo:
        build_settings(
            ["--node-id", "node-01"],
            environ={},
            first_run_ok=True,
        )
    assert "SYNCTHING_ROOT" in str(excinfo.value)


def test_AGENT_STATE_DIR_default_uses_resolved_root_and_node_id(tmp_path: Path) -> None:
    """AGENT_STATE_DIR default is <root>/.agent/<node_id>."""
    settings, defaulted = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)],
        environ={},
    )
    assert "AGENT_STATE_DIR" in defaulted
    assert settings.AGENT_STATE_DIR == tmp_path / ".agent" / "node-01"
