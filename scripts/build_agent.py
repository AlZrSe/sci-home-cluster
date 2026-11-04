#!/usr/bin/env python3
"""Build the standalone worker-agent executable (issue #164)."""

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

from agent.build_config import (  # noqa: E402
    BUILD_DIR,
    COLLECT_SUBMODULES,
    DIST_DIR,
    EXECUTABLE_NAME,
    HIDDEN_IMPORTS,
    PATHS,
    PYINSTALLER_FLAGS,
    SYNCTHING_OUTPUT_PATHS,
)


def _default_target() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def _binary_name(target: str) -> str:
    return EXECUTABLE_NAME + (".exe" if target == "windows" else "")


def build_pyinstaller_argv(target: str) -> list[str]:
    """Build the PyInstaller argv for *target* from ``agent.build_config``."""
    argv: list[str] = ["pyinstaller"]
    argv.extend(PYINSTALLER_FLAGS)
    argv += ["--name", EXECUTABLE_NAME]
    for path in PATHS:
        argv += ["--paths", path]
    argv += [
        "--specpath",
        BUILD_DIR,
        "--distpath",
        DIST_DIR,
        "--workpath",
        f"{BUILD_DIR}/work",
    ]
    for submodule in COLLECT_SUBMODULES:
        argv += ["--collect-submodules", submodule]
    for hidden in HIDDEN_IMPORTS:
        argv += ["--hidden-import", hidden]
    argv.append("scripts/run_agent_entry.py")
    return argv


def _run_pyinstaller(argv: list[str], env: dict[str, str]) -> int:
    """Run PyInstaller, falling back to ``python -m PyInstaller``."""
    try:
        completed = subprocess.run(argv, check=False, env=env, cwd=REPO_ROOT)
    except FileNotFoundError:
        fallback = [sys.executable, "-m", "PyInstaller", *argv[1:]]
        completed = subprocess.run(fallback, check=False, env=env, cwd=REPO_ROOT)
    return completed.returncode


def publish(target: str) -> Path:
    """Copy the built binary into the Syncthing folder root."""
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
    parser.add_argument(
        "--target", choices=["linux", "windows"], default=_default_target()
    )
    parser.add_argument("--publish", action="store_true")
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

    if args.publish:
        dest = publish(args.target)
        print(f"published: {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
