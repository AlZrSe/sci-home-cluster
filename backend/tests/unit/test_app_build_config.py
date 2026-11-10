"""Build configuration contract (issue #165)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_CONFIG_PATH = REPO_ROOT / "backend" / "build_config.py"

pytestmark = pytest.mark.unit


def _load_build_config():
    spec = importlib.util.spec_from_file_location("build_config_app", BUILD_CONFIG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_config_defines_entry_point() -> None:
    bc = _load_build_config()
    assert bc.ENTRY_SCRIPT == "scripts/run_app_entry.py"
    assert (REPO_ROOT / bc.ENTRY_SCRIPT).is_file()


def test_build_config_executable_name() -> None:
    bc = _load_build_config()
    assert bc.EXECUTABLE_NAME == "shc"


def test_build_config_onefile_and_console() -> None:
    bc = _load_build_config()
    assert bc.ONEFILE is True
    assert bc.CONSOLE is True


def test_build_config_targets_include_windows_and_linux() -> None:
    bc = _load_build_config()
    assert "linux" in bc.TARGETS
    assert "windows" in bc.TARGETS


def test_build_config_data_files_embed_frontend() -> None:
    bc = _load_build_config()
    dests = [dest for _, dest in bc.DATA_FILES]
    assert "frontend" in dests
    frontend_entries = [e for e in bc.DATA_FILES if e[1] == "frontend"]
    assert frontend_entries and frontend_entries[0][0] == "frontend/.output/public"


def test_build_config_data_files_include_alembic() -> None:
    bc = _load_build_config()
    data = {(s, d) for s, d in bc.DATA_FILES}
    assert ("backend/alembic.ini", "backend") in data
    assert ("backend/alembic", "backend/alembic") in data


def test_build_config_hidden_imports_include_required_modules() -> None:
    bc = _load_build_config()
    for required in (
        "uvicorn",
        "fastapi",
        "sqlalchemy",
        "aiosqlite",
        "alembic",
        "pydantic",
        "pydantic_settings",
        "jose",
        "passlib",
        "watchdog",
        "portalocker",
        "yaml",
    ):
        assert required in bc.HIDDEN_IMPORTS, required


def test_build_config_collects_backend_and_shared() -> None:
    bc = _load_build_config()
    assert "backend" in bc.COLLECT_SUBMODULES
    assert "shared" in bc.COLLECT_SUBMODULES


def test_build_config_cli_flags_match_entry_point() -> None:
    bc = _load_build_config()
    for flag in ("--host", "--port", "--log-level"):
        assert flag in bc.CLI_FLAGS


def test_build_config_exit_codes() -> None:
    bc = _load_build_config()
    assert bc.EXIT_CODES == {0, 1}


def test_launcher_help_runs_from_source_checkout() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_app_entry.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    for flag in ("--host", "--port", "--log-level"):
        assert flag in result.stdout, flag
