"""Build configuration contract (issue #164)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_CONFIG_PATH = REPO_ROOT / "agent" / "build_config.py"

pytestmark = pytest.mark.unit


def _load_build_config():
    spec = importlib.util.spec_from_file_location("build_config", BUILD_CONFIG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_config_defines_entry_point() -> None:
    bc = _load_build_config()
    assert bc.ENTRY_SCRIPT == "scripts/run_agent_entry.py"
    assert (REPO_ROOT / bc.ENTRY_SCRIPT).is_file()


def test_build_config_executable_name() -> None:
    bc = _load_build_config()
    assert bc.EXECUTABLE_NAME == "run-agent"


def test_build_config_onefile_and_console() -> None:
    bc = _load_build_config()
    assert bc.ONEFILE is True
    assert bc.CONSOLE is True


def test_build_config_targets_include_windows_and_linux() -> None:
    bc = _load_build_config()
    assert "linux" in bc.TARGETS
    assert "windows" in bc.TARGETS


def test_build_config_syncthing_output_locations() -> None:
    bc = _load_build_config()
    assert bc.SYNCTHING_OUTPUT_PATHS["linux"] == "syncthing/run-agent"
    assert bc.SYNCTHING_OUTPUT_PATHS["windows"] == "syncthing/run-agent.exe"


def test_build_config_hidden_imports_include_required_modules() -> None:
    bc = _load_build_config()
    for required in ("watchdog", "psutil", "yaml", "shared", "agent"):
        assert any(
            required in item for item in bc.HIDDEN_IMPORTS + bc.COLLECT_SUBMODULES
        ), required


def test_build_config_cli_flags_match_run_agent() -> None:
    bc = _load_build_config()
    for flag in ("--node-id", "--syncthing-root", "--log-level"):
        assert flag in bc.CLI_FLAGS


def test_build_config_exit_codes_include_documented_values() -> None:
    bc = _load_build_config()
    assert bc.EXIT_CODES == {0, 1, 2, 3, 4}


def test_launcher_help_runs_from_source_checkout() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_agent_entry.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    for flag in ("--node-id", "--syncthing-root", "--log-level"):
        assert flag in result.stdout, flag
