"""Build configuration for the standalone worker-agent executable.

This module is the single source of truth for the PyInstaller invocation
(issue #164). It is imported by ``scripts/build_agent.py`` and by the unit
tests in ``agent/tests/test_build_config.py``. It is deliberately a plain
module with no PyInstaller import so it can be loaded on any machine,
including ones without PyInstaller installed.
"""

from __future__ import annotations

from typing import Final

#: Repository-root-relative path to the launcher passed to PyInstaller.
ENTRY_SCRIPT: Final[str] = "scripts/run_agent_entry.py"

#: Base name of the produced executable. PyInstaller appends ``.exe`` on
#: Windows automatically; do not include an extension here.
EXECUTABLE_NAME: Final[str] = "run-agent"

#: PyInstaller mode.
ONEFILE: Final[bool] = True

#: Console application (not windowed).
CONSOLE: Final[bool] = True

#: Repository-root-relative directory PyInstaller writes the binary to.
DIST_DIR: Final[str] = "dist"

#: Repository-root-relative directory PyInstaller writes spec/work files to.
BUILD_DIR: Final[str] = "build/agent"

#: Module search paths passed to PyInstaller (repository root).
PATHS: Final[list[str]] = ["."]

#: Submodules PyInstaller must collect in full.
COLLECT_SUBMODULES: Final[list[str]] = ["agent", "shared"]

#: Third-party modules that PyInstaller's static analysis can miss.
HIDDEN_IMPORTS: Final[list[str]] = [
    "watchdog",
    "watchdog.events",
    "watchdog.observers",
    "watchdog.observers.api",
    "psutil",
    "yaml",
    "pydantic",
    "pydantic_settings",
    "portalocker",
]

#: Root of the Syncthing shared folder, relative to the repository root.
SYNCTHING_ROOT_DIR: Final[str] = "syncthing"

#: Final artefact paths inside the Syncthing folder, per target platform.
SYNCTHING_OUTPUT_PATHS: Final[dict[str, str]] = {
    "linux": "syncthing/run-agent",
    "windows": "syncthing/run-agent.exe",
}

#: Platforms the CI matrix builds.
TARGETS: Final[list[str]] = ["linux", "windows"]

#: CLI flags the frozen binary must accept (parity with ``run-agent``).
CLI_FLAGS: Final[list[str]] = ["--node-id", "--syncthing-root", "--log-level"]

#: Process exit codes the frozen binary must propagate unchanged.
EXIT_CODES: Final[set[int]] = {0, 1, 2, 3, 4}

#: PyInstaller invocation flags that are always present.
PYINSTALLER_FLAGS: Final[list[str]] = [
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
]
