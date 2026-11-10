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

#: Frontend build output directory (relative to repo root) bundled as data.
FRONTEND_DATA: Final[str] = "frontend/.output/public"

#: Data files bundled into the executable. Each tuple is
#: (source path relative to repo root, destination directory inside the bundle).
#: The frontend entry is the *built* SPA output, not frontend/src; CI must run
#: `npm run build` first.
DATA_FILES: Final[list[tuple[str, str]]] = [
    (FRONTEND_DATA, "frontend"),
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
