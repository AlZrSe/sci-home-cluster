# Spec: Portable Binary for Windows and Linux with Integrated Frontend and Backend

**Issue**: #165 — "Create portable binary for Windows and Linux with integrated frontend and backend"
**Labels**: `enhancement`, `linux`, `windows`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-11-10
**Baseline**: `main` (post #164)
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
| `npm run typecheck` / `npm run lint` (frontend) | unaffected — no frontend source change |

The changes in this spec must not move any of those needles. Backend, agent and
frontend gates stay green. No new *runtime* Python dependencies are introduced;
PyInstaller is a **build-time** dependency only (already declared by #164 as the
`build` extra). The `syncthing/` directory, `dist/` and `build/` stay gitignored
and are never committed.

---

## 1. Verified Problem Statement

### 1.1 The application needs a Python *and* a Node toolchain on the host

`README.md` and `docker/Dockerfile` describe the only supported ways to run the
app today: an editable install (`pip install -e .`), a built frontend
(`npm run build` in SPA mode) and a running `uvicorn backend.main:app`. An
operator on Windows or Linux without Python or Node cannot run the cluster at all.
There is no single-file artefact, unlike the worker agent (#164).

### 1.2 The frontend is a build artefact, not source

`backend/core/frontend.py` (`register_frontend`) serves `frontend/.output/public`
in SPA mode and requires `_shell.html` to exist. The Docker image builds the
frontend in a Node stage and copies only `.output/public` into the image
(`docker/Dockerfile` stage 1 → `COPY --from=frontend ... /app/frontend`). A
frozen build must do the same: ship the *built* SPA output, not `frontend/src`.
A checkout whose `frontend/.output/public` is absent cannot be frozen into a
working app binary.

### 1.3 The bundle root is not the repository root at runtime

PyInstaller onefile extracts to a temporary directory exposed as `sys._MEIPASS`.
`backend/core/config.py` derives `_REPO_ROOT = Path(__file__).resolve().parents[2]`,
which in a frozen process points *inside* the read-only extraction tree. Two
consequences follow and both must be handled:

1. `DEFAULT_DATA_DIR` / `DEFAULT_STATE_DIR` would resolve to a read-only,
   ephemeral directory, so SQLite and the JWT signing key would be lost on every
   restart (and writes would fail). The existing `SHC_DATA_DIR` / `SHC_STATE_DIR`
   / `DATABASE_URL` override path already exists (#34) and must be used.
2. `FRONTEND_DIR` defaults to `_REPO_ROOT/frontend/.output/public`, i.e.
   `_MEIPASS/frontend/.output/public`, but the bundle will place assets at
   `_MEIPASS/frontend`. `SERVE_FRONTEND` defaults to `false`. Both must be
   overridden for the frozen process.

### 1.4 Alembic migrations live in files, not in code

`backend/core/database.py:run_migrations()` locates `alembic.ini` via
`Path(__file__).parent.parent / "alembic.ini"` and `alembic.ini` sets
`script_location = %(here)s/alembic`. PyInstaller does not know about either file
unless they are added as data, and `%(here)s` must still resolve inside the
bundle. A frozen build that omits them fails at first startup.

### 1.5 CI has no frontend-aware build job for the app

`.github/workflows/ci.yml` already contains a `build-agent` job (#164) that
checks out *without* submodules. The app build is the opposite: it needs the
frontend submodule built first, so it needs a Node step and `submodules: true`.
It must not reuse the agent job's checkout shape.

### 1.6 Acceptance requires testable build configuration

As with #164, there must be a single, importable source of truth for entry
point, executable name, output location, embedded frontend asset path and hidden
imports, so a unit test can assert them without invoking PyInstaller.

---

## 2. Root Cause / Constraints (summary)

Nothing in the application's runtime needs to change. The gap is packaging and
delivery: there is no build configuration, no launcher suitable for freezing, no
CI job that compiles for both platforms with a frontend build, and no handling of
`_MEIPASS` for writable state and frontend assets. The constraints are:

- **No cross-compilation.** Windows binaries must be built on a Windows runner;
  Linux binaries on a Linux runner.
- **No committed binaries.** `dist/`, `build/` and `syncthing/` are gitignored and
  must stay so.
- **Entry point contract.** The frozen executable starts the same FastAPI app as
  `uvicorn backend.main:app`, serves `/api/v1/health` and the SPA, and accepts
  the same environment variables. It additionally accepts `--host`, `--port` and
  `--log-level` flags.
- **Writable state.** SQLite and the signing key must never default to the bundle
  extraction directory. Defaults are resolved to a writable directory beside the
  executable, overridable by `SHC_DATA_DIR`, `SHC_STATE_DIR` and `DATABASE_URL`.
- **Frontend in-bundle.** `SERVE_FRONTEND=true` and `FRONTEND_DIR` point at the
  in-bundle frontend directory via `_MEIPASS`.
- **Hidden imports.** PyInstaller's static analysis misses dynamically imported
  submodules (uvicorn loops/protocols, SQLAlchemy dialects, Alembic, jose,
  passlib, OpenTelemetry). They must be listed explicitly.
- **Data files.** Frontend build output, `alembic.ini`, the `alembic/` tree and
  `pyproject.toml` must be added as data.
- **Reproducibility.** A clean checkout plus the documented commands must produce
  a working binary. Build caching must not be required.

---

## 3. Decisions

### D1 — Tooling: PyInstaller in `--onefile` mode

Same choice as #164 D1: PyInstaller needs no C compiler, runs on both target
runners, produces a single file. Nuitka is rejected for the same reasons.

### D2 — Launcher: `scripts/run_app_entry.py`

A root-adjacent launcher (under `scripts/` so it is not part of the importable
`backend` package, but added to the bundle root via `--paths .`). It makes the
bundle root importable, applies frozen-process defaults, parses a small CLI, then
delegates to uvicorn:

```python
# scripts/run_app_entry.py
"""Frozen-build entry point for the Scientific Home Cluster application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))


def _apply_frozen_defaults() -> None:
    """Point writable state and the frontend at sane locations inside a frozen build.

    Must run before backend.core.config is imported, because Settings is
    constructed at import time and reads these env vars then.
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", _BUNDLE_ROOT))
    app_home = Path(sys.executable).resolve().parent
    os.environ.setdefault("SERVE_FRONTEND", "true")
    os.environ.setdefault("FRONTEND_DIR", str(meipass / "frontend"))
    os.environ.setdefault("SHC_STATE_DIR", str(app_home / ".shc"))
    os.environ.setdefault("SHC_DATA_DIR", str(app_home / "data"))


def main(argv: list[str] | None = None) -> int:
    _apply_frozen_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = parser.parse_args(argv)

    # Settings reads LOG_LEVEL from the environment at import time.
    os.environ.setdefault("LOG_LEVEL", args.log_level.upper())

    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1)
```

The same file is the entry point used by the source checkout for smoke tests
(`python scripts/run_app_entry.py --help`), so behaviour is identical between
frozen and unfrozen runs. The `--help` path exits before importing uvicorn or
the backend, so it works on any machine.

### D3 — Single source of truth: `backend/build_config.py`

All build parameters live in one importable module so tests can assert them
without running PyInstaller. The build script and the CI workflow both read from
it. It is deliberately a plain module with no PyInstaller import.

```python
# backend/build_config.py
"""Build configuration for the portable application executable (issue #165)."""

from __future__ import annotations

from typing import Final

#: Repository-root-relative path to the launcher passed to PyInstaller.
ENTRY_SCRIPT: Final[str] = "scripts/run_app_entry.py"

#: Base name of the produced executable. PyInstaller appends ``.exe`` on
#: Windows automatically; do not include an extension here.
EXECUTABLE_NAME: Final[str] = "shc"

#: PyInstaller mode.
ONEFILE: Final[bool] = True

#: Console application (not windowed).
CONSOLE: Final[bool] = True

#: Repository-root-relative directory PyInstaller writes the binary to.
DIST_DIR: Final[str] = "dist"

#: Repository-root-relative directory PyInstaller writes spec/work files to.
BUILD_DIR: Final[str] = "build/app"

#: Module search paths passed to PyInstaller (repository root).
PATHS: Final[list[str]] = ["."]

#: Packages PyInstaller must collect in full.
COLLECT_SUBMODULES: Final[list[str]] = ["backend", "shared"]

#: Third-party modules that PyInstaller's static analysis can miss.
HIDDEN_IMPORTS: Final[list[str]] = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "starlette",
    "starlette.routing",
    "sqlalchemy",
    "sqlalchemy.ext.asyncio",
    "aiosqlite",
    "alembic",
    "alembic.config",
    "alembic.command",
    "alembic.runtime.migration",
    "pydantic",
    "pydantic_settings",
    "jose",
    "passlib",
    "passlib.handlers.bcrypt",
    "yaml",
    "psutil",
    "portalocker",
    "watchdog",
    "watchdog.events",
    "watchdog.observers",
    "watchdog.observers.api",
    "opentelemetry",
    "opentelemetry.sdk",
    "opentelemetry.instrumentation.fastapi",
]

#: Data files bundled into the executable. Each tuple is
#: (source path relative to repo root, destination directory inside the bundle).
#: The frontend entry is the *built* SPA output, not frontend/src; CI must run
#: `npm run build` first.
DATA_FILES: Final[list[tuple[str, str]]] = [
    ("frontend/.output/public", "frontend"),
    ("backend/alembic.ini", "backend"),
    ("backend/alembic", "backend/alembic"),
    ("pyproject.toml", "."),
]

#: Platforms the CI matrix builds.
TARGETS: Final[list[str]] = ["linux", "windows"]

#: CLI flags the frozen binary must accept.
CLI_FLAGS: Final[list[str]] = ["--host", "--port", "--log-level"]

#: Process exit codes the frozen binary propagates.
EXIT_CODES: Final[set[int]] = {0, 1}

#: PyInstaller invocation flags that are always present.
PYINSTALLER_FLAGS: Final[list[str]] = [
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
]

#: Default listening port when neither --port nor PORT is given.
DEFAULT_PORT: Final[int] = 8000

#: Default bind host when neither --host nor HOST is given.
DEFAULT_HOST: Final[str] = "0.0.0.0"
```

### D4 — Hidden imports

The build collects the two in-tree packages with `--collect-submodules` and adds
explicit `--hidden-import` entries for the third-party modules in
`HIDDEN_IMPORTS`. `tomllib` is standard library in Python 3.11+ and needs no
entry. `backend.*` and `shared.*` are covered by collection, not by hidden
imports. The frontend is *data*, not code, and is handled by `DATA_FILES`.

### D5 — Build script: `scripts/build_app.py`

A thin, cross-platform driver that reads `backend.build_config` and invokes
PyInstaller. It must work from a clean checkout on both Linux and Windows, *after*
the frontend has been built:

```text
python scripts/build_app.py [--target linux|windows] [--publish] [--dry-run]
```

Key behaviour:

- Resolves the target platform label from `--target` (default: `windows` if
  `sys.platform == "win32"` else `linux`).
- Builds the PyInstaller argv from `backend.build_config` (no duplicated
  literals), including `--add-data` entries using `os.pathsep` as the
  platform-correct separator.
- Sets `SOURCE_DATE_EPOCH=0` in the child environment for reproducible
  timestamps.
- Runs PyInstaller via `subprocess.run(..., check=True)` (with the
  `python -m PyInstaller` fallback from #164).
- Prints the produced binary path and size on success.
- Exits non-zero if PyInstaller fails, propagating its return code.
- `--publish` is **not** part of this issue's required flow (the app is
  delivered as a CI artefact, not through Syncthing); the flag is reserved and
  may copy the binary to a caller-supplied directory. The Syncthing path table
  from #164 is intentionally not reused.

### D6 — PyInstaller is a build extra, not a runtime dependency

Already declared by #164 (`pyproject.toml` `[project.optional-dependencies] build`).
No change needed; CI installs `".[dev,build]"`.

### D7 — CI: a matrix build job

A new job is added to `.github/workflows/ci.yml` (or a companion workflow
`build-app.yml`; the spec leaves the file choice to SWE but requires the job name
`build-app`). It must:

- Run on both `ubuntu-latest` and `windows-latest`.
- Check out **with submodules** (`submodules: true` / default), because the
  frontend submodule is required.
- Set up Node (the same `NODE_VERSION` used by the frontend jobs) and run
  `npm ci` + `npm run build` inside `frontend/`, verifying that
  `frontend/.output/public/_shell.html` exists before proceeding.
- Set up Python (the same `PYTHON_VERSION` used by the other jobs).
- Install `pip install -e ".[dev,build]"`.
- Run `python scripts/build_app.py --target <linux|windows>`.
- Smoke-test the frozen binary:
  - Linux: `./dist/shc --help`
  - Windows: `.\dist\shc.exe --help`
  The command must exit 0 and print `--host`, `--port` and `--log-level`.
- Upload the binary as an artefact named `app-<platform>` with path
  `dist/shc*`.
- Not be a required status check on `main` until it is green (mirrors how the
  `e2e` and `build-agent` jobs were introduced).

The two runners build in parallel. The frontend build step is the only network-
heavy part; it is isolated so a frontend hiccup fails the job before PyInstaller
runs.

### D8 — Smoke test of the frozen binary

CI runs, on each runner, the `--help` smoke test from D7. The full test suite is
**not** run against the frozen binary (it would require a live database and a
Syncthing folder and is out of scope).

### D9 — Documentation

- Root `README.md` quick-reference table gains a row:
  `Build app exe | python scripts/build_app.py` (after building the frontend).
- Root `README.md` gains a "Portable binary" subsection explaining that
  operators can run `shc` / `shc.exe` without Python or Node, that state is
  written beside the binary (or overridden via `SHC_DATA_DIR` / `SHC_STATE_DIR` /
  `DATABASE_URL`), and that the frontend is embedded.
- `docker/README.md` is unchanged; the binary is an additional distribution
  channel and must not break the Docker image path.
- `.gitignore` already ignores `dist/`, `build/` and `/syncthing/`; no change
  needed, but the spec records that build outputs must never be committed.

---

## 4. Code Shapes

### 4.1 `scripts/run_app_entry.py`

See §3 D2.

### 4.2 `backend/build_config.py`

See §3 D3.

### 4.3 `scripts/build_app.py`

```python
#!/usr/bin/env python3
"""Build the portable application executable (issue #165)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.build_config import (  # noqa: E402
    BUILD_DIR,
    COLLECT_SUBMODULES,
    DATA_FILES,
    DIST_DIR,
    EXECUTABLE_NAME,
    HIDDEN_IMPORTS,
    PATHS,
    PYINSTALLER_FLAGS,
)

def _default_target() -> str:
    return "windows" if sys.platform == "win32" else "linux"

def _binary_name(target: str) -> str:
    return EXECUTABLE_NAME + (".exe" if target == "windows" else "")

def build_pyinstaller_argv(target: str) -> list[str]:
    argv: list[str] = ["pyinstaller"]
    argv.extend(PYINSTALLER_FLAGS)
    argv += ["--name", EXECUTABLE_NAME]
    for path in PATHS:
        argv += ["--paths", path]
    argv += [
        "--specpath", BUILD_DIR,
        "--distpath", DIST_DIR,
        "--workpath", f"{BUILD_DIR}/work",
    ]
    for submodule in COLLECT_SUBMODULES:
        argv += ["--collect-submodules", submodule]
    for hidden in HIDDEN_IMPORTS:
        argv += ["--hidden-import", hidden]
    for src, dest in DATA_FILES:
        argv += ["--add-data", f"{src}{os.pathsep}{dest}"]
    argv.append("scripts/run_app_entry.py")
    return argv

def _run_pyinstaller(argv: list[str], env: dict[str, str]) -> int:
    try:
        completed = subprocess.run(argv, check=False, env=env, cwd=REPO_ROOT)
    except FileNotFoundError:
        fallback = [sys.executable, "-m", "PyInstaller", *argv[1:]]
        completed = subprocess.run(fallback, check=False, env=env, cwd=REPO_ROOT)
    return completed.returncode

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["linux", "windows"],
                        default=_default_target())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env.setdefault("SOURCE_DATE_EPOCH", "0")

    argv_list = build_pyinstaller_argv(args.target)

    if args.dry_run:
        print(" ".join(argv_list))
        return 0

    returncode = _run_pyinstaller(argv_list, env)
    if returncode != 0:
        return returncode

    built = Path(DIST_DIR) / _binary_name(args.target)
    print(f"built: {built} ({built.stat().st_size} bytes)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

Notes for SWE:

- `--add-data` uses `os.pathsep`, which is `;` on Windows and `:` on Linux. Do not
  hard-code either separator.
- `frontend/.output/public` must exist before the build; the script does not build
  the frontend itself. CI builds it first.
- `pyproject.toml` is bundled so `get_version_from_pyproject()` resolves the
  version inside the frozen process instead of falling back to `0.1.0`.
- `alembic.ini` and `backend/alembic/` are bundled so `run_migrations()` works
  from `_MEIPASS`.

### 4.4 Unit tests: `backend/tests/unit/test_app_build_config.py`

```python
"""Build configuration contract (issue #165)."""

from __future__ import annotations

import importlib.util
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
    for required in ("uvicorn", "fastapi", "sqlalchemy", "aiosqlite",
                     "alembic", "pydantic", "pydantic_settings", "jose",
                     "passlib", "watchdog", "portalocker", "yaml"):
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
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/run_app_entry.py", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    for flag in ("--host", "--port", "--log-level"):
        assert flag in result.stdout, flag
```

The tests are pure configuration assertions; they do not require PyInstaller or a
built frontend. `test_launcher_help_runs_from_source_checkout` runs the launcher
with the current interpreter and therefore works on any dev machine.

### 4.5 CI matrix (excerpt)

```yaml
  build-app:
    name: build-app (${{ matrix.target }})
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
          submodules: true

      - name: Set up Node ${{ env.NODE_VERSION }}
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}

      - name: Build frontend (SPA mode)
        working-directory: frontend
        run: |
          npm ci
          npm run build
          test -f .output/public/_shell.html

      - name: Set up Python ${{ env.PYTHON_VERSION }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install build dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev,build]"

      - name: Build app
        run: python scripts/build_app.py --target ${{ matrix.target }}

      - name: Smoke test frozen binary
        shell: bash
        run: |
          if [ "${{ matrix.target }}" = "linux" ]; then
            ./dist/shc --help
          else
            ./dist/shc.exe --help
          fi

      - name: Upload app binary
        uses: actions/upload-artifact@v4
        with:
          name: app-${{ matrix.target }}
          path: dist/shc*
```

The smoke-test step uses `shell: bash` on both runners so the `if/else` works
uniformly.

---

## 5. Acceptance Criteria (checklist for SWE/QA)

- [ ] **AC-1** `scripts/run_app_entry.py` exists and `python scripts/run_app_entry.py --help`
      exits 0 and prints `--host`, `--port`, `--log-level`.
- [ ] **AC-2** `backend/build_config.py` exists and exposes `ENTRY_SCRIPT`,
      `EXECUTABLE_NAME`, `ONEFILE`, `CONSOLE`, `DIST_DIR`, `BUILD_DIR`,
      `PATHS`, `COLLECT_SUBMODULES`, `HIDDEN_IMPORTS`, `DATA_FILES`,
      `TARGETS`, `CLI_FLAGS`, `EXIT_CODES`, `DEFAULT_PORT`, `DEFAULT_HOST`.
- [ ] **AC-3** `EXECUTABLE_NAME == "shc"` and `DATA_FILES` contains an entry
      whose destination is `"frontend"` and source is
      `"frontend/.output/public"`.
- [ ] **AC-4** `scripts/build_app.py` supports `--target` and `--dry-run`;
      `--dry-run` prints a PyInstaller command containing `--onefile`,
      `--name shc`, `--collect-submodules backend`,
      `--collect-submodules shared`, every entry of `HIDDEN_IMPORTS`, and at
      least one `--add-data` whose destination is `frontend`.
- [ ] **AC-5** On a Linux host (with the frontend built), `python scripts/build_app.py`
      produces `dist/shc`; on a Windows host, `dist/shc.exe`.
      Cross-build is not supported and is not attempted.
- [ ] **AC-6** The built binary, invoked with `--help`, exits 0 and prints the
      three documented flags.
- [ ] **AC-7** In a frozen run, `SERVE_FRONTEND` is on, `FRONTEND_DIR` points
      inside `sys._MEIPASS/frontend`, and `SHC_STATE_DIR` / `SHC_DATA_DIR` default
      to a writable directory beside the executable (not inside `_MEIPASS`),
      unless overridden by the environment.
- [ ] **AC-8** State persists across restarts: with the defaults, a second
      launch reads the same SQLite file and signing key; overriding
      `SHC_DATA_DIR`, `SHC_STATE_DIR` or `DATABASE_URL` redirects all state.
- [ ] **AC-9** The frozen binary serves `/api/v1/health` (200) and `/`
      (the SPA shell) on a single port, matching `uvicorn backend.main:app` with
      `SERVE_FRONTEND=true`.
- [ ] **AC-10** `pyproject.toml` declares a `build` optional-dependency group
      containing `pyinstaller` (version range `>=6.0.0,<7.0.0`) — already
      present from #164.
- [ ] **AC-11** CI contains a `build-app` job with a two-entry matrix
      (`ubuntu-latest`/`windows-latest`), `submodules: true`, a frontend build
      step, and a `--help` smoke test.
- [ ] **AC-12** `backend/tests/unit/test_app_build_config.py` passes on the
      pinned toolchain without PyInstaller installed or a built frontend.
- [ ] **AC-13** `ruff check .`, `ruff format --check .`, and `mypy .` remain
      clean after the change.
- [ ] **AC-14** `pytest --cov=backend` (including the new test) stays green.
- [ ] **AC-15** No binary artefact (`shc`, `shc.exe`) is committed; `git status`
      on a clean build shows only ignored paths under `dist/`, `build/`.

---

## 6. Test Scenarios

### Scenario 1 — Configuration assertions (unit, no PyInstaller, no frontend)

**Given** `backend/build_config.py`
**When** `pytest backend/tests/unit/test_app_build_config.py` runs
**Then** all assertions pass, including entry point path, name, onefile mode,
embedded frontend asset, alembic data files, hidden imports, collected packages,
CLI flags and exit codes.

### Scenario 2 — Launcher parity (unit)

**Given** a source checkout
**When** `python scripts/run_app_entry.py --help` runs
**Then** it exits 0 and prints `--host`, `--port`, `--log-level`.

### Scenario 3 — Dry run (unit-ish)

**Given** `scripts/build_app.py`
**When** `python scripts/build_app.py --dry-run --target linux` runs
**Then** it prints a single command line containing `--onefile`,
`--name shc`, `--collect-submodules backend`, `--collect-submodules shared`,
`--add-data` with a `frontend` destination, and each hidden import.

### Scenario 4 — Build (CI, Linux runner)

**Given** `ubuntu-latest` with frontend built and `.[dev,build]` installed
**When** `python scripts/build_app.py --target linux` runs
**Then** `dist/shc` exists, is executable, and `./dist/shc --help` exits 0
printing the three flags.

### Scenario 5 — Build (CI, Windows runner)

**Given** `windows-latest` with frontend built and `.[dev,build]` installed
**When** `python scripts/build_app.py --target windows` runs
**Then** `dist/shc.exe` exists and `.\dist\shc.exe --help` exits 0.

### Scenario 6 — Frozen runtime (manual/CI smoke)

**Given** a successful build
**When** `./dist/shc` runs with defaults
**Then** it binds a port, `/api/v1/health` returns 200, `/` returns the SPA
shell, state is written beside the binary, and a restart reuses it.

### Scenario 7 — Env override

**Given** a frozen binary
**When** it runs with `SHC_DATA_DIR`, `SHC_STATE_DIR` and `DATABASE_URL` set
**Then** all state lands in the overridden locations and none in the defaults.

### Scenario 8 — Regression

**Given** the change merged
**When** `pytest --cov=backend` and `ruff check . && ruff format --check . && mypy .`
run
**Then** both pass; coverage does not drop below 80%.

---

## 7. Risks and Open Questions

1. **Frontend build prerequisite.** A clean checkout without `frontend/.output/public`
   cannot be frozen. CI builds the frontend first; local developers must run
   `npm run build` in `frontend/`. The build script fails fast on the missing
   `--add-data` source rather than producing a broken binary.
2. **Binary size.** A onefile build bundles the interpreter plus the SPA assets
   (~60–100 MB). Expected and acceptable for a portable artefact.
3. **First-run extraction latency.** Onefile extracts to a temp dir on every
   launch. Acceptable for a long-running server.
4. **Read-only install location.** Defaults write beside the executable; if that
   directory is read-only, set `SHC_DATA_DIR` / `SHC_STATE_DIR` (or `DATABASE_URL`)
   to a writable path. Env overrides always win.
5. **Alembic in a bundle.** `script_location = %(here)s/alembic` resolves
   against the bundled `alembic.ini`; both files must be added as data. A missing
   file surfaces as a startup error, not a silent no-op.
6. **Version resolution.** `get_version_from_pyproject()` reads `_MEIPASS/pyproject.toml`;
   it is bundled as data so the version is correct rather than `0.1.0`.
7. **Windows SmartScreen / antivirus.** An unsigned `.exe` triggers SmartScreen and
   may be flagged by antivirus. Code signing is out of scope; document the
   "Run anyway" step.
8. **Required-check status.** The `build-app` job is added as a required check
   only after its first green run, to avoid blocking merges on a not-yet-proven
   job.
9. **Open question (SWE):** whether to also publish the app binary into the
   Syncthing folder. The issue lists it as optional; this spec keeps delivery to
   CI artefacts only. If SWE adds a publish step, it must reuse the path-traversal
   guard from #164.

---

## 8. Files to Create / Modify

### Create
- `scripts/run_app_entry.py` — frozen-build launcher.
- `backend/build_config.py` — single source of truth for build parameters.
- `scripts/build_app.py` — cross-platform build driver.
- `backend/tests/unit/test_app_build_config.py` — configuration contract tests.

### Modify
- `.github/workflows/ci.yml` — add `build-app` matrix job (or companion workflow).
- `README.md` — quick-reference row + "Portable binary" subsection.

### Do not modify
- `backend/core/config.py` — frozen defaults are applied in the launcher via env
  vars, so the config module and its tests are untouched.
- `backend/core/database.py` / `backend/core/frontend.py` — already support
  `_MEIPASS`-relative paths once env vars point at the bundle.
- `pyproject.toml` — the `build` extra already exists (#164).
- `.gitignore` — already ignores `dist/`, `build/`, `/syncthing/`.

---

## 9. Definition of Done

- [ ] All files in §8 created/modified.
- [ ] `backend/tests/unit/test_app_build_config.py` passes on the pinned
      toolchain without PyInstaller installed or a built frontend.
- [ ] `python scripts/run_app_entry.py --help` exits 0.
- [ ] `python scripts/build_app.py --dry-run` prints a valid PyInstaller command.
- [ ] `ruff check . && ruff format --check . && mypy .` clean.
- [ ] `pytest --cov=backend` green (coverage ≥ 80%).
- [ ] GitHub issue #165 updated: label `ready-for-grooming` removed,
      `groomed` added; comment links to this spec.
- [ ] PM accepts the implementation.

---

## References

- Issue #165 — this spec.
- Issue #164 — standalone worker-agent executable (template/prior art).
- Issue #34 — CWD-independent database/state paths (`SHC_DATA_DIR`,
  `SHC_STATE_DIR`, `DATABASE_URL`).
- Issue #56 — published signing-key rotation.
- `backend/core/frontend.py` — static SPA serving (`SERVE_FRONTEND`,
  `FRONTEND_DIR`, `_shell.html`).
- `backend/core/config.py` — `DEFAULT_DATA_DIR`, `DEFAULT_STATE_DIR`,
  `FRONTEND_DIR`, `SERVE_FRONTEND`.
- `backend/core/database.py` — `run_migrations()` and `alembic.ini` resolution.
- `docker/Dockerfile` — integrated frontend+backend image build (parallel
  channel, must keep working).
- PyInstaller documentation: `--onefile`, `--add-data`, `--collect-submodules`,
  `--hidden-import`, `sys._MEIPASS`, `SOURCE_DATE_EPOCH`.