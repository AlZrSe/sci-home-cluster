"""The entry point: one logging call, and flags that match the docs."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agent import run_agent
from agent.config import (
    EXIT_CONFIG_ERROR,
    EXIT_INTERNAL_ERROR,
    EXIT_OK,
    build_parser,
)
from agent.tests.conftest import REPO_ROOT

pytestmark = pytest.mark.unit

AGENTS_MD = REPO_ROOT / "AGENTS.md"
README_MD = REPO_ROOT / "README.md"

#: Installed console script entry point (``pyproject.toml`` ``[project.scripts]``).
CONSOLE_SCRIPT = "run-agent"

#: AST calls that would re-introduce a second logging path (issue #93 D2).
LOGGING_ENTRY_POINTS = {"basicConfig", "addHandler", "setLogRecordFactory"}


def module_source(name: str) -> str:
    return (REPO_ROOT / "agent" / f"{name}.py").read_text(encoding="utf-8")


def documented_agent_command(text: str) -> str:
    for line in text.splitlines():
        if CONSOLE_SCRIPT in line and line.strip().startswith("|"):
            match = re.search(r"`([^`]*--node-id[^`]*)`", line)
            assert match is not None, f"no documented command found in: {line!r}"
            return match.group(1)
    raise AssertionError(f"{CONSOLE_SCRIPT} is not in the quick-reference table")


# --- AC-1 -----------------------------------------------------------------


def test_run_agent_module_has_no_print_and_no_todo() -> None:
    source = module_source("run_agent")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ), f"print() at line {node.lineno}"
    assert "TODO" not in source


def test_run_agent_has_exactly_one_logging_entry_point() -> None:
    """D2: a handler installed anywhere else makes the node id disappear."""
    tree = ast.parse(module_source("run_agent"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                found.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                found.add(node.func.attr)
    assert "basicConfig" not in found
    assert "addHandler" not in found
    assert "setLogRecordFactory" not in found
    assert found & LOGGING_ENTRY_POINTS == set()
    assert "configure_logging" in found


def test_main_delegates_to_agent_run(monkeypatch: Any, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class FakeAgent:
        def __init__(self, settings: Any) -> None:
            seen["settings"] = settings

        async def run(self) -> int:
            seen["ran"] = True
            return 7

    monkeypatch.setattr(run_agent, "Agent", FakeAgent)
    exit_code = run_agent.main(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_path)]
    )
    assert exit_code == 7
    assert seen["ran"] is True
    assert seen["settings"].NODE_ID == "node-01"


def test_main_returns_zero_on_clean_shutdown(
    monkeypatch: Any, tmp_path: Path, preserve_root_logging: None
) -> None:
    class CleanAgent:
        def __init__(self, settings: Any) -> None:
            self.settings = settings

        async def run(self) -> int:
            return EXIT_OK

    monkeypatch.setattr(run_agent, "Agent", CleanAgent)
    assert run_agent.main(["--node-id", "n1", "--syncthing-root", str(tmp_path)]) == 0


def test_main_returns_one_on_config_error(tmp_path: Path, capsys: Any) -> None:
    exit_code = run_agent.main(["--syncthing-root", str(tmp_path)])
    assert exit_code == EXIT_CONFIG_ERROR
    assert "NODE_ID" in capsys.readouterr().err


def test_main_returns_one_on_an_unknown_cli_log_level(
    tmp_path: Path, capsys: Any
) -> None:
    """D-G: argparse's exit 2 must not escape a function that returns an int.

    ``main``'s docstring promises the process exit code and
    ``EXIT_CONFIG_ERROR`` is documented as the code for a configuration error,
    but a rejected flag raised ``SystemExit`` straight through it. Every other
    bad-input path returns 1.
    """
    exit_code = run_agent.main(
        [
            "--node-id",
            "node-01",
            "--syncthing-root",
            str(tmp_path),
            "--log-level",
            "LOUD",
        ]
    )
    assert exit_code == EXIT_CONFIG_ERROR
    assert "--log-level" in capsys.readouterr().err


def test_main_returns_one_on_an_unknown_flag(tmp_path: Path, capsys: Any) -> None:
    assert (
        run_agent.main(["--node-id", "node-01", "--nope", str(tmp_path)])
        == EXIT_CONFIG_ERROR
    )
    assert "--nope" in capsys.readouterr().err


def test_main_returns_zero_for_help(capsys: Any) -> None:
    """``--help`` is not a configuration error; it must stay a success."""
    assert run_agent.main(["--help"]) == EXIT_OK
    assert "--node-id" in capsys.readouterr().out


def test_main_returns_two_on_internal_error(
    monkeypatch: Any, tmp_path: Path, preserve_root_logging: None
) -> None:
    """Exit code 2 has to be reachable, or the table is a fiction."""

    class ExplodingAgent:
        def __init__(self, settings: Any) -> None:
            self.settings = settings

        async def run(self) -> int:
            raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(run_agent, "Agent", ExplodingAgent)
    assert (
        run_agent.main(["--node-id", "n1", "--syncthing-root", str(tmp_path)])
        == EXIT_INTERNAL_ERROR
    )


def test_main_returns_zero_on_keyboard_interrupt(
    monkeypatch: Any, tmp_path: Path, preserve_root_logging: None
) -> None:
    class InterruptedAgent:
        def __init__(self, settings: Any) -> None:
            self.settings = settings

        async def run(self) -> int:
            raise KeyboardInterrupt

    monkeypatch.setattr(run_agent, "Agent", InterruptedAgent)
    assert (
        run_agent.main(["--node-id", "n1", "--syncthing-root", str(tmp_path)])
        == EXIT_OK
    )


# --- AC-18 ----------------------------------------------------------------


def test_no_config_flag_is_offered() -> None:
    """The location is derived; there is nothing to point a flag at."""
    options = {
        option for action in build_parser()._actions for option in action.option_strings
    }
    assert "--config" not in options
    assert not any(name.startswith("--config") for name in options)
    assert "-c" not in options


def test_run_agent_help_matches_the_documented_flags() -> None:
    help_text = build_parser().format_help()
    for flag in ("--node-id", "--syncthing-root", "--log-level"):
        assert flag in help_text, flag
    assert "--config" not in help_text


def test_documented_command_is_accepted_by_the_parser() -> None:
    command = documented_agent_command(AGENTS_MD.read_text(encoding="utf-8"))
    argv = command.split()[1:]
    args = build_parser().parse_args(argv)
    assert args.node_id is not None


def test_agents_md_quick_reference_command_runs(tmp_path: Path) -> None:
    """Run the documented command for real, from outside the repository root."""
    root = tmp_path / "syncthing"
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)

    command = documented_agent_command(AGENTS_MD.read_text(encoding="utf-8"))
    argv = command.split()[1:]

    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "SYNCTHING_ROOT": str(root),
    }
    if sys.platform == "win32" and "USERPROFILE" in os.environ:
        env["USERPROFILE"] = os.environ["USERPROFILE"]

    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent.run_agent", *argv],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    collected: list[str] = []

    def drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            collected.append(line)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            assert process.poll() is None, f"the documented command exited: {collected}"
            if any("agent starting" in line for line in collected):
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"the documented agent never started: {collected!r}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=10)

    output = "".join(collected)
    startup = [line for line in collected if "agent starting" in line]
    assert startup, f"the documented agent never started: {output!r}"
    for line in startup:
        assert (
            "[node=node-01]" in line
        ), f"a rendered line an operator would read has no node id: {line!r}"


def test_readme_documents_the_worker_agent() -> None:
    text = README_MD.read_text(encoding="utf-8")
    assert CONSOLE_SCRIPT in text
    assert "nodes/agent.toml" in text
    assert "--node-id" in text


def test_readme_states_the_config_file_is_read_once() -> None:
    """No hot reload: an operator who edits the file must know to restart."""
    text = README_MD.read_text(encoding="utf-8").lower()
    assert "once" in text
    assert "restart" in text


def test_help_output_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "agent.run_agent", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0
    assert "--node-id" in completed.stdout


def test_run_agent_is_a_registered_console_script() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'{CONSOLE_SCRIPT} = "agent.run_agent:main"' in text
