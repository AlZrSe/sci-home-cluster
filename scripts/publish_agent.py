#!/usr/bin/env python3
"""Publish the built agent binary and assets to the Syncthing folder."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.build_config import (  # noqa: E402
    SYNCTHING_OUTPUT_PATHS,
)


def _binary_name(target: str) -> str:
    return "run-agent" + (".exe" if target == "windows" else "")


def publish(target: str) -> Path:
    """Copy the built binary and assets into the Syncthing folder root."""
    src = Path("dist") / _binary_name(target)
    if not src.is_file():
        raise FileNotFoundError(f"built binary not found: {src}")
    dest_rel = SYNCTHING_OUTPUT_PATHS["windows" if sys.platform == "win32" else "linux"]
    dest = (Path(__file__).resolve().parent.parent / dest_rel).resolve()
    root = Path.cwd().resolve()
    if root not in dest.parents and dest != root:
        raise ValueError(f"refusing to publish outside repository root: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    # Copy .env.example and .env.sample
    env_template_src = Path("agent") / ".env.example"
    if not env_template_src.is_file():
        env_template_src = Path("agent") / ".env.sample"
    if env_template_src.is_file():
        shutil.copy2(env_template_src, Path("syncthing") / ".env.example")
        shutil.copy2(env_template_src, Path("syncthing") / ".env.sample")
    # Copy run scripts as .example files
    for script_name in ("run-agent.cmd", "run-agent.sh", "run-agent.ps1"):
        src_script = Path("scripts") / script_name
        if src_script.is_file():
            shutil.copy2(src_script, Path("syncthing") / (script_name + ".example"))
    # Copy sample calculation program to bin/ directory
    syncthing_bin = Path("syncthing") / "bin"
    syncthing_bin.mkdir(parents=True, exist_ok=True)
    for sample_name in ("sample_calc.py", "sample_calc", "sample_calc.cmd", "README.md"):
        src_sample = Path("samples") / sample_name
        if src_sample.is_file():
            shutil.copy2(src_sample, syncthing_bin / sample_name)
    print(f"Published to {dest}")
    return Path(dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish agent to Syncthing folder")
    parser.add_argument(
        "--target",
        choices=["linux", "windows"],
        default="windows" if os.name == "nt" else "linux",
    )
    args = parser.parse_args()

    try:
        dest = publish(args.target)
        print(f"published: {dest}")
        return 0
    except Exception as exc:
        print(f"Publish failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
