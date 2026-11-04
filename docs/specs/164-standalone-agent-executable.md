# Spec: Compile Worker Agent into Standalone Executables (Windows + Linux)

**Issue**: #164 — "Compile worker agent into standalone executables for Windows and Linux and distribute via Syncthing"
**Labels**: `agent`, `enhancement`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-11-04
**Baseline**: `main` (post #162)
**Measured on**: Windows 11, pinned toolchain per `AGENTS.md`

**This file is the authoritative input for the SWE cycle.** It is self-sufficient:
§3 carries the decisions, §4 carries the code shapes, §5 is the acceptance
checklist. No code is implemented by this spec.

---

## 0. Baseline, as re-measured by PM

| Gate | On `main` |
|---|---|
| `ruff check . && ruff format --check . && mypy .` | expected clean |
| `pytest --cov=backend` (incl. `agent/tests`) | expected green; compose suite skipped unless `SHC_COMPOSE_TESTS=1` |
| `npm run typecheck` / `npm run lint` (frontend) | unaffected — no frontend change |

The changes in this spec must not move any of those needles. Backend and agent
gates stay green. No new runtime Python dependencies are introduced; PyInstaller
is a **build-time** dependency only. The `syncthing/` directory stays gitignored
and is never committed.

---

## 1. Verified Problem Statement

### 1.1 The agent needs a Python interpreter on every node

`agent/README.md` §"Prerequisites" requires "Python 3.11+ with the project
installed (`pip install -e .`)". `pyproject.toml` registers the console script
`run-agent = "agent.run_agent:main"`. An operator must clone the repository,
create a venv, and install the package before any node can run. There is no
single-file artefact.

### 1.2 The agent is not self-contained

The agent imports two in-tree packages (`agent`, `shared`) plus third-party
libraries (`watchdog`, `psutil`, `pyyaml`, `pydantic`, `pydantic-settings`,
`portalocker`). A frozen build must carry all of them; missing any produces an
`ImportError` at startup on a node that has no Python.

### 1.3 The entry point is a module, not a script

`agent/run_agent.py` is written to be invoked as `python -m agent.run_agent` or
via the installed `run-agent` console script. Its imports are absolute
(`from agent.config import ...`, `from agent.loop import Agent`). Running the
file directly as a script (`python agent/run_agent.py`) puts `agent/` on
`sys.path`, not the repository root, so `import agent` fails. A frozen build
needs a root-level launcher that puts the bundle root on `sys.path` before
importing.

### 1.4 CI has no Windows runner for agent tests today

`.github/workflows/ci.yml` notes (bucket C, issue #124) that
`agent/tests/test_loop.py` sends itself `SIGTERM` and dies on Windows. That is
why the workflow has no Windows matrix entry for the *test* suite. A *build*
job is different: it does not run the agent's tests, it only compiles. A
Windows build runner is therefore safe and necessary, because PyInstaller does
not cross-compile.

### 1.5 The Syncthing folder is the distribution channel

`syncthing/` at the repository root is the shared folder (gitignored, see
`.gitignore` `/syncthing/`). Operators already sync it to every node. Dropping
`run-agent` / `run-agent.exe` at its root makes the binaries replicate without
any new transport. The artefacts must **not** be committed (they are large,
platform-specific, and rebuilt by CI).

### 1.6 Acceptance requires testable build configuration

The issue's last acceptance criterion is "Tests verify the build configuration
(entry point, name, output location)." There must be a single, importable source
of truth for those three values so a unit test can assert them without invoking
PyInstaller.

---

## 2. Root Cause / Constraints (summary)

Nothing in the agent's runtime needs to change. The gap is packaging and
delivery: there is no build configuration, no launcher suitable for freezing,
no CI job that compiles for both platforms, and no step that publishes the
result into the Syncthing folder. The constraints are:

- **No cross-compilation.** Windows binaries must be built on a Windows runner;
  Linux binaries on a Linux runner.
- **No committed binaries.** `syncthing/` is gitignored and must stay so.
- **Entry point contract.** The frozen executable must accept the same CLI
  (`--node-id`, `--syncthing-root`, `--log-level`) and return the same exit
  codes (0 clean, 1 config error, 2 internal error, 3 watcher dead, 4 grace
  expired) as `run-agent`. PyInstaller's default behaviour (forward `sys.argv`,
  propagate `sys.exit` return code) satisfies this as long as the launcher calls
  `main()` and exits with its return value.
- **Hidden imports.** PyInstaller's static analysis can miss submodules that are
  imported dynamically or only referenced as strings. The build must explicitly
  pull `agent.*`, `shared.*`, and the third-party modules named in §3 D4.
- **Reproducibility.** A clean checkout plus the documented command must produce
  a working binary. Build caching must not be required.

---

## 3. Decisions

### D1 — Tooling: PyInstaller in `--onefile` mode

PyInstaller is the candidate named in the issue and is the lower-friction
choice: it needs no C compiler, runs on both target runners, and produces a
single file. Nuitka is rejected for this issue because it requires a C
compiler on every runner and longer build times for no benefit at this stage.

The build command shape:

```
pyinstaller \
  --noconfirm --clean \
  --onefile \
  --console \
  --name run-agent \
  --paths . \
  --specpath build/agent \
  --distpath dist \
  --workpath build/agent/work \
  --collect-submodules agent \
  --collect-submodules shared \
  --hidden-import watchdog \
  --hidden-import watchdog.events \
  --hidden-import watchdog.observers \
  --hidden-import watchdog.observers.api \
  --hidden-import psutil \
  --hidden-import yaml \
  --hidden-import pydantic \
  --hidden-import pydantic_settings \
  --hidden-import portalocker \
  scripts/run_agent_entry.py
```

`--console` (the default on Windows, stated explicitly) keeps a console
attached so `--help` and log output work the way they do for `run-agent`.

### D2 — Launcher: `scripts/run_agent_entry.py`

A new root-adjacent launcher (under `scripts/` so it is not part of the
importable `agent` package, but added to the bundle root via `--paths .`).
It must make the bundle root importable before importing the agent, because the
frozen script's own directory is not the bundle root:

```python
# scripts/run_agent_entry.py
"""Frozen-build entry point for the worker agent.

PyInstaller executes this file as ``__main__`` from inside the bundle, so its
own directory (``scripts/``) is on ``sys.path`` but the bundle root is not.
Insert the bundle root first, then delegate to :func:`agent.run_agent.main`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))

from agent.run_agent import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
```

The same file is the entry point used by the source checkout for smoke tests
(`python scripts/run_agent_entry.py --help`), so behaviour is identical between
frozen and unfrozen runs.

### D3 — Single source of truth: `agent/build_config.py`

All build parameters live in one importable module so tests can assert them
without running PyInstaller. The build script and the CI workflow both read
from it.

```python
# agent/build_config.py
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
```

### D4 — Hidden imports

The build collects the two in-tree packages with `--collect-submodules` and
adds explicit `--hidden-import` entries for the third-party modules in
`HIDDEN_IMPORTS`. `tomllib` is standard library in Python 3.11+ and needs no
entry. `pydantic`/`pydantic_settings` are normally auto-detected but are listed
defensively because they use dynamic attribute access internally.

### D5 — Build script: `scripts/build_agent.py`

A thin, cross-platform driver that reads `agent.build_config` and invokes
PyInstaller. It must work from a clean checkout on both Linux and Windows:

```python
# scripts/build_agent.py
"""Build the standalone worker-agent executable.

Usage:
    python scripts/build_agent.py [--target linux|windows] [--publish]

--target   selects the platform label used for Syncthing output naming.
           The actual binary format is determined by the host OS; building a
           Windows binary requires a Windows host. Default: current OS.
--publish  copy the built binary into the Syncthing folder root
           (syncthing/run-agent or syncthing/run-agent.exe).
--dry-run  print the PyInstaller command and exit without running it.
"""
```

Key behaviour:

- Resolves the target platform label from `--target` (default:
  `windows` if `sys.platform == "win32"` else `linux`).
- Builds the PyInstaller argv from `agent.build_config` (no duplicated
  literals).
- Sets `SOURCE_DATE_EPOCH=0` in the child environment for reproducible
  timestamps.
- Runs PyInstaller via `subprocess.run(..., check=True)`.
- With `--publish`, copies `dist/run-agent[.exe]` to the path in
  `SYNCTHING_OUTPUT_PATHS[target]`, creating `syncthing/` if needed. It refuses
  to overwrite a path that is not under the repository root (path-traversal
  guard).
- Prints the produced binary path and size on success.
- Exits non-zero if PyInstaller fails, propagating its return code.

### D6 — PyInstaller is a build extra, not a runtime dependency

`pyproject.toml` gains a new optional dependency group:

```toml
[project.optional-dependencies]
build = [
    "pyinstaller>=6.0.0,<7.0.0",
]
```

Runtime dependencies are unchanged. CI installs `".[dev,build]"`. Developers who
only run the agent never install PyInstaller.

### D7 — CI: a matrix build job

A new job is added to `.github/workflows/ci.yml` (or a companion workflow
`build-agent.yml`; the spec leaves the file choice to SWE but requires the
job name `build-agent`). It must:

- Run on both `ubuntu-latest` and `windows-latest`.
- Check out without submodules (`submodules: false`) — the build needs
  `agent/` and `shared/`, which are in the main repository; the frontend
  submodule is not needed and fetching it would waste time and couple the build
  to a frontend network hiccup.
- Set up Python (the same `PYTHON_VERSION` used by the other jobs).
- Install `pip install -e ".[dev,build]"`.
- Run `python scripts/build_agent.py --target <linux|windows>` where the target
  is derived from `runner.os`.
- Run `python scripts/build_agent.py --publish --target <linux|windows>` so the
  binary lands at `syncthing/run-agent` (Linux) or `syncthing/run-agent.exe`
  (Windows).
- Upload the Syncthing-folder binary as an artefact named `agent-<platform>`
  with path `syncthing/run-agent*`.
- Not be a required status check on `main` until it is green; it is added to
  the required set only after the first successful run. (This mirrors how the
  `e2e` job was introduced.)

Because PyInstaller cannot cross-compile, the matrix is the only way to produce
both binaries. The two runners build in parallel.

A final, Linux-only `publish-agent` job (allowed to depend on `build-agent`,
because it is delivery rather than a gate) downloads both artefacts and places
them into a single `syncthing/` tree, then uploads that tree as one artefact
`agent-binaries`. This is the artefact an operator (or a deployment step) drops
into the live Syncthing folder.

### D8 — Smoke test of the frozen binary

CI runs, on each runner, a minimal smoke test after the build:

```
./dist/run-agent --help          # Linux
.\dist\run-agent.exe --help      # Windows
```

The command must exit 0 and print `--node-id`, `--syncthing-root`, and
`--log-level`. This proves the launcher resolves imports and the CLI contract
survives freezing. The full test suite is **not** run against the frozen
binary (it would require a live Syncthing folder and is out of scope).

### D9 — Documentation

- `agent/README.md` §"Installation" gains a subsection "Standalone executable"
  explaining that operators can copy `run-agent` / `run-agent.exe` from the
  Syncthing folder instead of installing Python, and that the binary accepts
  the same flags and exit codes.
- The root `README.md` quick-reference table gains a row:
  `Build agent exe | python scripts/build_agent.py`.
- `.gitignore` already ignores `/syncthing/` and `build/`/`dist/`; no change
  needed, but the spec records that the build outputs must never be committed.

---

## 4. Code Shapes

### 4.1 `scripts/run_agent_entry.py`

See §3 D2.

### 4.2 `agent/build_config.py`

See §3 D3.

### 4.3 `scripts/build_agent.py`

```python
#!/usr/bin/env python3
"""Build the standalone worker-agent executable (issue #164)."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.build_config import (  # noqa: E402
    BUILD_DIR,
    COLLECT_SUBMODULES,
    DIST_DIR,
    EXECUTABLE_NAME,
    HIDDEN_IMPORTS,
    PATHS,
    PYINSTALLER_FLAGS,
    SYNCTHING_OUTPUT_PATHS,
    SYNCTHING_ROOT_DIR,
)

def _default_target() -> str:
    return "windows" if sys.platform == "win32" else "linux"

def _binary_name(target: str) -> str:
    return EXECUTABLE_NAME + (".exe" if target == "windows" else "")

def build_pyinstaller_argv(target: str) -> list[str]:
    argv = ["pyinstaller"]
    argv.extend(PYINSTALLER_FLAGS)
    argv += ["--name", EXECUTABLE_NAME]
    for path in PATHS:
        argv += ["--paths", path]
    argv += ["--specpath", BUILD_DIR, "--distpath", DIST_DIR,
             "--workpath", f"{BUILD_DIR}/work"]
    for submodule in COLLECT_SUBMODULES:
        argv += ["--collect-submodules", submodule]
    for hidden in HIDDEN_IMPORTS:
        argv += ["--hidden-import", hidden]
    argv.append("scripts/run_agent_entry.py")
    return argv

def publish(target: str) -> Path:
    src = Path(DIST_DIR) / _binary_name(target)
    if not src.is_file():
        raise FileNotFoundError(f"built binary not found: {src}")
    dest_rel = SYNCTHING_OUTPUT_PATHS[target]
    dest = (REPO_ROOT / dest_rel).resolve()
    root = REPO_ROOT.resolve()
    if root not in dest.parents and dest != root:
        raise ValueError(f"refusing to publish outside repository root: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["linux", "windows"],
                        default=_default_target())
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env.setdefault("SOURCE_DATE_EPOCH", "0")

    if args.dry_run:
        print(" ".join(build_pyinstaller_argv(args.target)))
        return 0

    subprocess.run(build_pyinstaller_argv(args.target), check=True, env=env,
                   cwd=REPO_ROOT)
    built = Path(DIST_DIR) / _binary_name(args.target)
    print(f"built: {built} ({built.stat().st_size} bytes)")

    if args.publish:
        dest = publish(args.target)
        print(f"published: {dest}")

    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
```

Notes for SWE:

- `pyinstaller` is invoked as a console script. If it is not on `PATH`, fall back to
  `python -m PyInstaller` (implement with `sys.executable`).
- `--workpath` uses forward slashes; PyInstaller accepts them on Windows.
- The path-traversal guard in `publish()` compares resolved paths.

### 4.4 Unit tests: `agent/tests/test_build_config.py`

```python
"""Build configuration contract (issue #164)."""

from __future__ import annotations

import importlib.util
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
        assert any(required in item for item in bc.HIDDEN_IMPORTS
                   + bc.COLLECT_SUBMODULES), required


def test_build_config_cli_flags_match_run_agent() -> None:
    bc = _load_build_config()
    for flag in ("--node-id", "--syncthing-root", "--log-level"):
        assert flag in bc.CLI_FLAGS


def test_build_config_exit_codes_include_documented_values() -> None:
    bc = _load_build_config()
    assert bc.EXIT_CODES == {0, 1, 2, 3, 4}


def test_launcher_help_runs_from_source_checkout() -> None:
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/run_agent_entry.py", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    for flag in ("--node-id", "--syncthing-root", "--log-level"):
        assert flag in result.stdout, flag
```

The tests are pure configuration assertions; they do not require PyInstaller.
`test_launcher_help_runs_from_source_checkout` runs the launcher with the current
interpreter and therefore works on any dev machine.

### 4.5 CI matrix (excerpt)

```yaml
  build-agent:
    name: build-agent (${{ matrix.target }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-latest
            target: linux
          - os: windows-latest
            target: windows
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: false
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      - run: python -m pip install --upgrade pip
      - run: pip install -e ".[dev,build]"
      - run: python scripts/build_agent.py --target ${{ matrix.target }}
      - run: python scripts/build_agent.py --publish --target ${{ matrix.target }}
      - run: |
          if [ "${{ matrix.target }}" = "linux" ]; then
            ./dist/run-agent --help
          else
            .\dist\run-agent.exe --help
          fi
        shell: bash
      - uses: actions/upload-artifact@v4
        with:
          name: agent-${{ matrix.target }}
          path: syncthing/run-agent*
```

The smoke-test step uses `shell: bash` on both runners (GitHub provides bash on
Windows) so the `if/else` works uniformly.

---

## 5. Acceptance Criteria (checklist for SWE/QA)

- [ ] **AC-1** `scripts/run_agent_entry.py` exists and `python scripts/run_agent_entry.py --help`
      exits 0 and prints `--node-id`, `--syncthing-root`, `--log-level`.
- [ ] **AC-2** `agent/build_config.py` exists and exposes `ENTRY_SCRIPT`,
      `EXECUTABLE_NAME`, `ONEFILE`, `CONSOLE`, `DIST_DIR`, `BUILD_DIR`,
      `PATHS`, `COLLECT_SUBMODULES`, `HIDDEN_IMPORTS`, `SYNCTHING_ROOT_DIR`,
      `SYNCTHING_OUTPUT_PATHS`, `TARGETS`, `CLI_FLAGS`, `EXIT_CODES`.
- [ ] **AC-3** `EXECUTABLE_NAME == "run-agent"` and
      `SYNCTHING_OUTPUT_PATHS == {"linux": "syncthing/run-agent",
      "windows": "syncthing/run-agent.exe"}`.
- [ ] **AC-4** `scripts/build_agent.py` supports `--target`, `--publish`,
      `--dry-run`; `--dry-run` prints a PyInstaller command containing
      `--onefile`, `--name run-agent`, `--collect-submodules agent`,
      `--collect-submodules shared`, and every entry of `HIDDEN_IMPORTS`.
- [ ] **AC-5** On a Linux host, `python scripts/build_agent.py` produces
      `dist/run-agent`; on a Windows host, `dist/run-agent.exe`. (Cross-build
      is not supported and is not attempted.)
- [ ] **AC-6** The built binary, invoked with `--help`, exits 0 and prints the
      three documented flags.
- [ ] **AC-7** `--publish` copies the binary to `syncthing/run-agent` (Linux)
      or `syncthing/run-agent.exe` (Windows), creating `syncthing/` if needed,
      and refuses paths outside the repository root.
- [ ] **AC-8** `pyproject.toml` declares a `build` optional-dependency group
      containing `pyinstaller` (version range `>=6.0.0,<7.0.0`).
- [ ] **AC-9** CI contains a `build-agent` job with a two-entry matrix
      (`ubuntu-latest`/`windows-latest`), `submodules: false`, and a
      `--help` smoke test.
- [ ] **AC-10** `agent/tests/test_build_config.py` passes on the pinned
      toolchain without PyInstaller installed.
- [ ] **AC-11** `ruff check .`, `ruff format --check .`, and `mypy .` remain
      clean after the change.
- [ ] **AC-12** `pytest --cov=backend` (including `agent/tests`) stays green.
- [ ] **AC-13** No binary artefact (`run-agent`, `run-agent.exe`) is committed;
      `git status` on a clean build shows only ignored paths under `dist/`,
      `build/`, and `syncthing/`.

---

## 6. Test Scenarios

### Scenario 1 — Configuration assertions (unit, no PyInstaller)

**Given** `agent/build_config.py`
**When** `pytest agent/tests/test_build_config.py` runs
**Then** all assertions pass, including entry point path, name, onefile mode,
Syncthing output locations, hidden imports, CLI flags, and exit codes.

### Scenario 2 — Launcher parity (unit)

**Given** a source checkout
**When** `python scripts/run_agent_entry.py --help` runs
**Then** it exits 0 and prints the three flags (same as
`python -m agent.run_agent --help`).

### Scenario 3 — Dry run (unit-ish)

**Given** `scripts/build_agent.py`
**When** `python scripts/build_agent.py --dry-run --target linux` runs
**Then** it prints a single command line containing `--onefile`,
`--name run-agent`, `--collect-submodules agent`,
`--collect-submodules shared`, and each of `watchdog`, `psutil`, `yaml`.

### Scenario 4 — Build (CI, Linux runner)

**Given** `ubuntu-latest` with `.[dev,build]` installed
**When** `python scripts/build_agent.py --target linux` runs
**Then** `dist/run-agent` exists, is executable, and `./dist/run-agent --help`
exits 0 printing the three flags.

### Scenario 5 — Build (CI, Windows runner)

**Given** `windows-latest` with `.[dev,build]` installed
**When** `python scripts/build_agent.py --target windows` runs
**Then** `dist/run-agent.exe` exists and `.\dist\run-agent.exe --help` exits 0.

### Scenario 6 — Publish

**Given** a successful build
**When** `python scripts/build_agent.py --publish --target linux` runs
**Then** `syncthing/run-agent` exists and is byte-identical to
`dist/run-agent`. A second invocation overwrites cleanly.

### Scenario 7 — Path guard

**Given** a tampered `SYNCTHING_OUTPUT_PATHS` entry resolving outside the repo
**When** `publish()` runs
**Then** it raises `ValueError` and writes nothing.

### Scenario 8 — Regression

**Given** the change merged
**When** `pytest --cov=backend` and `ruff check . && ruff format --check . && mypy .`
run
**Then** both pass; coverage does not drop below 80%.

---

## 7. Risks and Open Questions

1. **PyInstaller version availability.** The build extra pins `>=6.0.0,<7.0.0`.
   SWE records the exact resolved version in the CI log. If 6.x lacks a wheel
   for a future Python, widen the range in a follow-up; do not vendor.
2. **Binary size.** A onefile build bundles the interpreter (~30–50 MB). This
   is expected and acceptable for a Syncthing-distributed artefact.
3. **Windows SmartScreen.** An unsigned `.exe` triggers SmartScreen. Code
   signing is out of scope for this issue; document the "Run anyway" step in
   `agent/README.md`.
4. **Antivirus false positives.** PyInstaller onefile binaries are occasionally
   flagged. If this becomes a problem, switch to `--onedir` in a follow-up;
   the config's `ONEFILE` flag and tests make that a one-line change.
5. **First-run extraction latency.** Onefile extracts to a temp dir on every
   launch (a few seconds). Acceptable for a long-running agent.
6. **`--publish` on a runner with no `syncthing/`.** The directory is created;
   it is uploaded as an artefact, not committed.
7. **Required-check status.** The `build-agent` job is added as a required
   check only after its first green run, to avoid blocking merges on a
   not-yet-proven job.

---

## 8. Files to Create / Modify

### Create
- `scripts/run_agent_entry.py` — frozen-build launcher.
- `agent/build_config.py` — single source of truth for build parameters.
- `scripts/build_agent.py` — cross-platform build driver.
- `agent/tests/test_build_config.py` — configuration contract tests.
- `.github/workflows/build-agent.yml` **or** a `build-agent` job in
  `.github/workflows/ci.yml` — matrix build + publish + smoke test.

### Modify
- `pyproject.toml` — add `[project.optional-dependencies] build`.
- `agent/README.md` — "Standalone executable" subsection.
- `README.md` — quick-reference row for building the agent executable.

### Do not modify
- `agent/run_agent.py` — its CLI and exit codes are the contract the frozen
  binary must match.
- `.gitignore` — already ignores `dist/`, `build/`, `/syncthing/`.

---

## 9. Definition of Done

- [ ] All files in §8 created/modified.
- [ ] `agent/tests/test_build_config.py` passes on the pinned toolchain without
      PyInstaller installed.
- [ ] `python scripts/run_agent_entry.py --help` exits 0.
- [ ] `python scripts/build_agent.py --dry-run` prints a valid PyInstaller
      command.
- [ ] `ruff check . && ruff format --check . && mypy .` clean.
- [ ] `pytest --cov=backend` green (coverage ≥ 80%).
- [ ] GitHub issue #164 updated: label `ready-for-grooming` removed,
      `groomed` added; comment links to this spec.
- [ ] PM accepts the implementation.

---

## References

- Issue #164 — this spec.
- Issue #93 — agent skeleton (`agent/run_agent.py`, exit codes).
- Issue #107 — dev-install / wheel availability (build extra must install
  cleanly on Windows without build tools).
- Issue #124 — why CI has no Windows *test* matrix entry (does not apply to
  the build job).
- PyInstaller documentation: `--onefile`, `--collect-submodules`,
  `--hidden-import`, `SOURCE_DATE_EPOCH`.
- `.gitignore` — `/syncthing/`, `build/`, `dist/`.