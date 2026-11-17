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


def test_detects_missing_env_at_agent_dir(tmp_path: Path, monkeypatch: Any) -> None:
    """Missing .env at agent dir triggers first-run flow."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    # Ensure repo root has no .env
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        with patch("agent.run_agent.REPO_ROOT", repo_root):
            with patch("agent.run_agent.ENV_FILE", repo_root / ".env"):
                is_first_run, missing = _detect_first_run(
                    [],  # argv
                    {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"},  # environ
                )
                assert is_first_run is True
                assert missing == set()


def test_detects_existing_env_at_agent_dir(tmp_path: Path, monkeypatch: Any) -> None:
    """.env exists at agent dir skips prompt when all required vars present."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        is_first_run, missing = _detect_first_run(
            [],  # argv
            {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"},  # environ
        )
        assert is_first_run is False
        assert missing == set()


def test_detects_missing_required_vars_with_env_file(tmp_path: Path) -> None:
    """.env exists at agent dir but required vars missing ->
    first run with missing vars.
    """
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env").write_text("# empty or incomplete\n", encoding="utf-8")
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        # Neither NODE_ID nor SYNCTHING_ROOT in environ
        is_first_run, missing = _detect_first_run([], {})
        assert is_first_run is True
        assert missing == REQUIRED_VARS

        # Only NODE_ID in environ
        is_first_run, missing = _detect_first_run([], {"NODE_ID": "node-01"})
        assert is_first_run is True
        assert missing == {"SYNCTHING_ROOT"}

        # Only SYNCTHING_ROOT in environ
        is_first_run, missing = _detect_first_run([], {"SYNCTHING_ROOT": "/tmp"})
        assert is_first_run is True
        assert missing == {"NODE_ID"}


def test_detects_fallback_to_repo_root(tmp_path: Path) -> None:
    """.env missing at agent dir but exists at repo root -> not first run."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    # No .env at agent dir

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".env").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    (repo_root / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        with patch("agent.run_agent.REPO_ROOT", repo_root):
            with patch("agent.run_agent.ENV_FILE", repo_root / ".env"):
                is_first_run, missing = _detect_first_run(
                    [],  # argv
                    {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"},  # environ
                )
                assert is_first_run is False
                assert missing == set()


# --- _prompt_create_env --------------------------------------------------------


def test_prompt_create_env_accepts_y(tmp_path: Path) -> None:
    """Prompt returns True on 'y' input."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    # Mock selectors to return ready immediately
    with patch("selectors.DefaultSelector") as mock_selector:
        mock_sel = MagicMock()
        mock_selector.return_value = mock_sel
        mock_sel.select.return_value = [(None, selectors.EVENT_READ)]

        with patch("sys.stdin.readline", return_value="y\n"):
            with patch("sys.stderr.write", lambda _: None):
                with patch("agent.run_agent.AGENT_DIR", agent_dir):
                    result = _prompt_create_env()

    assert result is True


def test_prompt_create_env_rejects_n(tmp_path: Path) -> None:
    """Prompt returns False on 'n' input."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("selectors.DefaultSelector") as mock_selector:
        mock_sel = MagicMock()
        mock_selector.return_value = mock_sel
        mock_sel.select.return_value = [(None, selectors.EVENT_READ)]

        with patch("sys.stdin.readline", return_value="n\n"):
            with patch("sys.stderr.write", lambda _: None):
                with patch("agent.run_agent.AGENT_DIR", agent_dir):
                    result = _prompt_create_env()

    assert result is False


def test_prompt_create_env_timeout(tmp_path: Path) -> None:
    """Prompt returns False on timeout."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("selectors.DefaultSelector") as mock_selector:
        mock_sel = MagicMock()
        mock_selector.return_value = mock_sel
        mock_sel.select.return_value = []  # Timeout - no input ready

        with patch("sys.stderr.write", lambda _: None):
            with patch("agent.run_agent.AGENT_DIR", agent_dir):
                result = _prompt_create_env()

    assert result is False


def test_prompt_create_env_ctrl_c(tmp_path: Path) -> None:
    """Prompt returns False on KeyboardInterrupt."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("sys.stdin.readline", side_effect=KeyboardInterrupt):
        with patch("sys.stderr.write", lambda _: None):
            with patch("agent.run_agent.AGENT_DIR", agent_dir):
                result = _prompt_create_env()

    assert result is False


def test_prompt_create_env_missing_example(tmp_path: Path) -> None:
    """Prompt returns False when .env.example is missing."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    # No .env.example

    with patch("sys.stderr.write", lambda _: None):
        with patch("agent.run_agent.AGENT_DIR", agent_dir):
            result = _prompt_create_env()

    assert result is False


# --- _run_first_run_flow -------------------------------------------------------


def test_first_run_flow_interactive_creates_env(tmp_path: Path) -> None:
    """Interactive TTY, user confirms -> creates .env and proceeds."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    # Ensure repo root has no .env
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        with patch("agent.run_agent.REPO_ROOT", repo_root):
            with patch("agent.run_agent.ENV_FILE", repo_root / ".env"):
                with patch("agent.run_agent.ENV_EXAMPLE", agent_dir / ".env.example"):
                    with patch("sys.stdin.isatty", return_value=True):
                        with patch("selectors.DefaultSelector") as mock_selector:
                            mock_sel = MagicMock()
                            mock_selector.return_value = mock_sel
                            mock_sel.select.return_value = [
                                (None, selectors.EVENT_READ)
                            ]

                            with patch("sys.stdin.readline", return_value="y\n"):
                                with patch("sys.stderr.write", lambda _: None):
                                    result = _run_first_run_flow(
                                        [],
                                        {
                                            "NODE_ID": "node-01",
                                            "SYNCTHING_ROOT": "/tmp",
                                        },
                                    )

    assert result is True
    assert (agent_dir / ".env").exists()
    # .env should be empty since we no longer copy from .env.example
    content = (agent_dir / ".env").read_text(encoding="utf-8")
    assert content == ""


def test_first_run_flow_interactive_declines(tmp_path: Path) -> None:
    """Interactive TTY, user declines -> proceeds without .env."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        with patch("sys.stdin.isatty", return_value=True):
            with patch("selectors.DefaultSelector") as mock_selector:
                mock_sel = MagicMock()
                mock_selector.return_value = mock_sel
                mock_sel.select.return_value = [(None, selectors.EVENT_READ)]

                with patch("sys.stdin.readline", return_value="n\n"):
                    with patch("sys.stderr.write", lambda _: None):
                        result = _run_first_run_flow(
                            [],
                            {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"},
                        )

    assert result is True
    assert not (agent_dir / ".env").exists()


def test_first_run_flow_non_interactive_logs_warning(
    tmp_path: Path, capsys: Any
) -> None:
    """Non-TTY skips prompt, logs warning to stderr."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    # Ensure repo root has no .env
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        with patch("agent.run_agent.REPO_ROOT", repo_root):
            with patch("agent.run_agent.ENV_FILE", repo_root / ".env"):
                with patch("sys.stdin.isatty", return_value=False):
                    result = _run_first_run_flow(
                        [],
                        {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"},
                    )

    assert result is True
    captured = capsys.readouterr()
    assert "WARNING: Running in non-interactive mode" in captured.err
    assert "SYNCTHING_ROOT via environment or CLI flags" in captured.err


def test_first_run_flow_already_configured_skips(tmp_path: Path) -> None:
    """.env exists at agent dir with required vars -> skips first-run flow entirely."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        with patch("sys.stderr.write", lambda _: None):
            result = _run_first_run_flow(
                [],
                {"NODE_ID": "node-01", "SYNCTHING_ROOT": "/tmp"},
            )

    assert result is True


# --- build_settings integration ------------------------------------------------


def test_required_vars_fail_fast_with_existing_messages(tmp_path: Path) -> None:
    """Missing NODE_ID/SYNCTHING_ROOT raises ConfigError with actionable messages."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        # Missing SYNCTHING_ROOT (validated first - needed to locate config file)
        with pytest.raises(ConfigError) as excinfo:
            build_settings(
                [],
                first_run_ok=True,
            )
        assert "SYNCTHING_ROOT is required" in str(excinfo.value)

        # Missing NODE_ID (when SYNCTHING_ROOT provided)
        with pytest.raises(ConfigError) as excinfo:
            build_settings(
                ["--syncthing-root", "/tmp"],
                first_run_ok=True,
            )
        assert "NODE_ID is required" in str(excinfo.value)


# --- Default configuration behavior --------------------------------------------


def test_optional_vars_default_and_warn(tmp_path: Path) -> None:
    """When first_run_ok=True, optional vars get defaults and warning is emitted."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        settings, defaulted = build_settings(
            ["--node-id", "node-01", "--syncthing-root", "/tmp"],
            first_run_ok=True,
        )

    # Required vars provided
    assert settings.NODE_ID == "node-01"
    assert settings.SYNCTHING_ROOT == Path("/tmp").resolve()

    # Optional vars defaulted
    assert "LOG_LEVEL" in defaulted
    assert settings.LOG_LEVEL == "INFO"
    assert "SHELL" in defaulted
    assert settings.SHELL == "auto"
    assert "SHUTDOWN_GRACE_S" in defaulted
    assert "FOLDER_WATCH_INTERVAL_S" in defaulted
    assert "FOLDER_RETRY_MAX_S" in defaulted
    assert "WATCHER_LIVENESS_INTERVAL_S" in defaulted
    assert "HEARTBEAT_INTERVAL_S" in defaulted
    assert "HEARTBEAT_JITTER_S" in defaulted
    assert "AGENT_STATE_DIR" in defaulted


def test_explicit_cli_suppresses_warning(tmp_path: Path) -> None:
    """Explicit CLI flag suppresses warning for that setting."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        settings, defaulted = build_settings(
            [
                "--node-id",
                "node-01",
                "--syncthing-root",
                "/tmp",
                "--log-level",
                "DEBUG",
                "--shell",
                "bash",
            ],
            first_run_ok=True,
        )

    assert "LOG_LEVEL" not in defaulted
    assert settings.LOG_LEVEL == "DEBUG"
    assert "SHELL" not in defaulted
    assert settings.SHELL == "bash"


def test_explicit_env_suppresses_warning(tmp_path: Path) -> None:
    """Explicit env var suppresses warning for that setting."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        settings, defaulted = build_settings(
            ["--node-id", "node-01", "--syncthing-root", "/tmp"],
            environ={"AGENT_LOG_LEVEL": "DEBUG", "AGENT_SHELL": "bash"},
            first_run_ok=True,
        )

    assert "LOG_LEVEL" not in defaulted
    assert settings.LOG_LEVEL == "DEBUG"
    assert "SHELL" not in defaulted
    assert settings.SHELL == "bash"


def test_toml_suppresses_warning(tmp_path: Path) -> None:
    """TOML config file suppresses warning for that setting."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    syncthing_root = tmp_path / "syncthing"
    syncthing_root.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    # Create nodes/agent.toml in syncthing root
    nodes_dir = syncthing_root / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "agent.toml").write_text(
        'AGENT_LOG_LEVEL = "DEBUG"\nAGENT_SHELL = "bash"\n',
        encoding="utf-8",
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        settings, defaulted = build_settings(
            ["--node-id", "node-01", "--syncthing-root", str(syncthing_root)],
            first_run_ok=True,
        )

    assert "LOG_LEVEL" not in defaulted
    assert settings.LOG_LEVEL == "DEBUG"
    assert "SHELL" not in defaulted
    assert settings.SHELL == "bash"


def test_cli_beats_env_beats_toml_beats_default(tmp_path: Path) -> None:
    """Precedence: CLI > env > TOML > default."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    syncthing_root = tmp_path / "syncthing"
    syncthing_root.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )
    nodes_dir = syncthing_root / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "agent.toml").write_text(
        'AGENT_LOG_LEVEL = "DEBUG"\nAGENT_SHELL = "bash"\n',
        encoding="utf-8",
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        # CLI beats env beats TOML
        settings, _ = build_settings(
            [
                "--node-id",
                "node-01",
                "--syncthing-root",
                str(syncthing_root),
                "--log-level",
                "INFO",
            ],
            environ={"AGENT_LOG_LEVEL": "WARNING", "AGENT_SHELL": "bash"},
            first_run_ok=True,
        )
        assert settings.LOG_LEVEL == "INFO"
        assert settings.SHELL == "bash"

        # Env beats TOML
        settings, _ = build_settings(
            ["--node-id", "node-01", "--syncthing-root", str(syncthing_root)],
            environ={"AGENT_LOG_LEVEL": "WARNING", "AGENT_SHELL": "bash"},
            first_run_ok=True,
        )
        assert settings.LOG_LEVEL == "WARNING"
        assert settings.SHELL == "bash"

        # TOML beats default
        settings, _ = build_settings(
            ["--node-id", "node-01", "--syncthing-root", str(syncthing_root)],
            first_run_ok=True,
        )
        assert settings.LOG_LEVEL == "DEBUG"
        assert settings.SHELL == "bash"


def test_first_run_ok_allows_missing_required_for_optional_defaults(
    tmp_path: Path,
) -> None:
    """first_run_ok=True allows optional defaults but still requires
    NODE_ID/SYNCTHING_ROOT.
    """
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        # Missing NODE_ID still fails
        with pytest.raises(ConfigError) as excinfo:
            build_settings(
                ["--syncthing-root", "/tmp"],
                first_run_ok=True,
            )
        assert "NODE_ID is required" in str(excinfo.value)

        # Missing SYNCTHING_ROOT still fails
        with pytest.raises(ConfigError) as excinfo:
            build_settings(
                ["--node-id", "node-01"],
                first_run_ok=True,
            )
        assert "SYNCTHING_ROOT is required" in str(excinfo.value)


def test_AGENT_STATE_DIR_default_uses_resolved_root_and_node_id(
    tmp_path: Path,
) -> None:
    """AGENT_STATE_DIR default is <syncthing_root>/.agent/<node_id>."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    syncthing_root = tmp_path / "syncthing"
    syncthing_root.mkdir()
    (agent_dir / ".env.example").write_text(
        "NODE_ID=node-01\nSYNCTHING_ROOT=/tmp\n", encoding="utf-8"
    )

    with patch("agent.run_agent.AGENT_DIR", agent_dir):
        settings, defaulted = build_settings(
            ["--node-id", "node-01", "--syncthing-root", str(syncthing_root)],
            first_run_ok=True,
        )

    assert "AGENT_STATE_DIR" in defaulted
    expected = syncthing_root.resolve() / ".agent" / "node-01"
    assert settings.AGENT_STATE_DIR == expected
