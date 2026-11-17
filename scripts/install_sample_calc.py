"""Idempotent installer for the sample calculation program into SYNCTHING_ROOT/bin."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install sample_calc into SYNCTHING_ROOT/bin"
    )
    parser.add_argument("--syncthing-root", default=os.environ.get("SYNCTHING_ROOT"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.syncthing_root:
        print("error: --syncthing-root or SYNCTHING_ROOT is required", file=sys.stderr)
        return 1
    root = Path(args.syncthing_root)
    src_dir = Path(__file__).resolve().parents[1] / "samples"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    for src in src_dir.iterdir():
        if not src.is_file():
            continue
        if src.name.startswith("job-") and src.name.endswith(".yaml"):
            continue
        if src.name.startswith("job-") and src.name.endswith(".yml"):
            continue
        dst = bin_dir / src.name
        shutil.copy2(src, dst)
        if os.name != "nt" and src.name in ("sample_calc", "sample_calc.py"):
            mode = dst.stat().st_mode
            dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed sample_calc into {bin_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
