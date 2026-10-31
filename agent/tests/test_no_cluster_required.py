"""The agent needs no cluster, no network, and no credentials (US-5, #92, AC-13..16)."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.tests.conftest import REPO_ROOT

pytestmark = pytest.mark.unit

AGENT_DIR = REPO_ROOT / "agent"
PYPROJECT = REPO_ROOT / "pyproject.toml"

SHIPPED_MODULES = [
    "commands",
    "claim",
    "config",
    "executor",
    "hardware",
    "logging_config",
    "log_writer",
    "loop",
    "paths",
    "process",
    "run_agent",
    "supervisor",
    "terminal",
    "watcher",
]

#: Issue #93 AC-15. The agent is file-only by decision (#92); this is the
#: mechanical enforcement of that decision.
NETWORK_MODULES = {
    "httpx",
    "requests",
    "urllib",
    "socket",
    "websockets",
    "websocket",
    "jose",
    "jwt",
    "aiohttp",
    "http",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
}

#: ``psutil`` and ``gputil`` arrived with hardware detection (issue #95, spec
#: Q1): psutil for CPU and memory, gputil for GPUs behind a guarded import. Both
#: are declared dependencies in ``pyproject.toml`` and neither can speak a
#: protocol -- they read /proc, /sys and the NVML library. The allowlist stays
#: pinned, so the next third-party import has to be argued for here rather than
#: arriving unnoticed.
THIRD_PARTY_ALLOWED = {"pydantic", "pydantic_settings", "watchdog", "psutil", "gputil"}

#: The subset of that which may be imported at **module scope**. ``psutil`` is a
#: hard dependency that is always installed and does nothing at import;
#: ``gputil`` is declared but not installed everywhere, so it stays behind the
#: guarded lazy import in ``agent/hardware.py`` -- see
#: ``test_a_third_party_module_is_imported_at_module_scope_only_if_allowed``.
THIRD_PARTY_AT_MODULE_SCOPE = {"pydantic", "pydantic_settings", "watchdog", "psutil"}

PROJECT_MODULES = {"agent", "shared", "backend", "cli"}


def agent_sources() -> list[Path]:
    return sorted(AGENT_DIR.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# --- AC-13: the tests are actually collected ------------------------------


def test_pyproject_testpaths_include_agent_tests() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'testpaths = ["backend/tests", "agent/tests"]' in text


def test_pyproject_coverage_measures_the_agent() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'source = ["backend", "agent"]' in text
    assert "agent/tests/*" in text


def test_pyproject_excludes_the_agent_test_package_from_wheels() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    line = next(
        line
        for line in text.splitlines()
        if line.strip().startswith("exclude = [") and "agent.tests" in line
    )
    for existing in (
        "test*",
        "docs*",
        "docker*",
        "frontend*",
        "stubs*",
        "node_modules*",
    ):
        assert f'"{existing}"' in line


# --- AC-15: no network anywhere under agent/ ------------------------------


def test_agent_imports_no_network_module() -> None:
    offenders: dict[str, set[str]] = {}
    for path in agent_sources():
        hit = imported_roots(path) & NETWORK_MODULES
        if hit:
            offenders[str(path.relative_to(REPO_ROOT))] = hit
    assert offenders == {}, f"the agent is file-only by decision (#92): {offenders}"


def third_party_roots(roots: set[str]) -> set[str]:
    return roots - set(sys.stdlib_module_names) - PROJECT_MODULES


def module_scope_imports(path: Path) -> set[str]:
    """Third-party roots imported at module scope only, ignoring function bodies.

    ``ast.walk`` (what :func:`imported_roots` uses) cannot tell "imported when
    the module is loaded" from "imported when a function is called", and the
    difference is load-bearing: a third-party import at module scope runs on
    every ``import agent.<anything>``, while one inside a function runs only when
    that function is reached.
    """
    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return third_party_roots(found)


def test_every_third_party_import_is_expected() -> None:
    """The whole third-party surface of the shipped package, pinned."""
    third_party: set[str] = set()
    for name in SHIPPED_MODULES:
        roots = imported_roots(AGENT_DIR / f"{name}.py")
        for root in roots:
            if root in sys.stdlib_module_names or root in PROJECT_MODULES:
                continue
            third_party.add(root)
    assert third_party == THIRD_PARTY_ALLOWED


def test_a_third_party_module_is_imported_at_module_scope_only_if_allowed() -> None:
    """Being on the allowlist does not buy a module-scope import.

    ``gputil`` is the case that matters: it is declared in ``pyproject.toml``,
    it is **not installed** in every environment, and ``getGPUs()`` raises on a
    machine with no NVIDIA driver (issue #95 Q1). ``agent/hardware.py`` imports
    it inside ``_gputil()`` so its absence is a supported state rather than an
    ``ImportError`` at agent start-up. The first test above cannot express that
    distinction -- it accepts the import at any scope -- so the narrower
    constraint is pinned here rather than left to good intentions.
    """
    at_module_scope: set[str] = set()
    for name in SHIPPED_MODULES:
        at_module_scope |= module_scope_imports(AGENT_DIR / f"{name}.py")

    assert at_module_scope == THIRD_PARTY_AT_MODULE_SCOPE, (
        "third-party imports at module scope run on every import of the agent; "
        "anything else must be imported lazily inside the function that needs "
        f"it. Module scope: {sorted(at_module_scope)}, allowed: "
        f"{sorted(THIRD_PARTY_AT_MODULE_SCOPE)}"
    )
    assert "gputil" in THIRD_PARTY_ALLOWED, (
        "gputil is still an expected dependency; it is only its import that is "
        "confined to a lazy call"
    )


def test_shipped_modules_are_the_documented_set() -> None:
    present = {
        path.stem for path in AGENT_DIR.glob("*.py") if path.name != "__init__.py"
    }
    assert present == set(SHIPPED_MODULES)


# --- AC-16: every module imports with no cluster environment --------------


def test_every_agent_module_imports_without_cluster_environment(
    tmp_path: Path,
) -> None:
    script = (
        "".join(f"import agent.{name}\n" for name in SHIPPED_MODULES) + "print('ok')"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "SYNCTHING_ROOT": str(tmp_path / "does-not-exist"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if sys.platform == "win32" and "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


@pytest.mark.slow
def test_agent_suite_passes_with_hostile_environment(tmp_path: Path) -> None:
    """AC-14/AC-16, in a subprocess so it cannot recurse into itself."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "SYNCTHING_ROOT": str(tmp_path / "no-such-folder"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if sys.platform == "win32" and "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "agent/tests",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--deselect",
            "agent/tests/test_no_cluster_required.py::"
            "test_agent_suite_passes_with_hostile_environment",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stdout[-4000:]


def test_no_module_level_settings_object() -> None:
    """No shipped module may build anything at import time.

    ``backend/core/config.py`` ends with ``settings = Settings()``, which is why
    its settings object cannot be exercised in isolation. The agent has exactly
    one construction site, ``build_settings`` (issue #93 §5.1).
    """
    for name in SHIPPED_MODULES:
        tree = ast.parse((AGENT_DIR / f"{name}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                assert (
                    "settings" not in targets
                ), f"agent/{name}.py builds a module-level settings object"
            assert not isinstance(node, ast.Expr) or not isinstance(
                node.value, ast.Call
            ), f"agent/{name}.py calls something at import time"
