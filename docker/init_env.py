#!/usr/bin/env python3
"""Seed the repository-root ``.env`` so ``docker compose up -d`` just works.

Two things have to be set before the stack will start, and neither should
require hand-editing:

``SHARED_TOKEN``
    The shared bearer token. ``docker-compose.yaml`` requires it and refuses to
    start without it, because unset means the API answers
    ``SHARED_TOKEN_NOT_CONFIGURED`` to every login. It is generated here with
    :func:`secrets.token_hex` when absent - 32 bytes, 64 hex characters.
    An existing value is NEVER overwritten, so re-running this is safe and
    does not log anybody out.

``SYNCTHING_ROOT``
    The host folder the backend and every agent share. Defaults to
    ``./syncthing``, relative to this repository, which compose resolves
    against the project directory. That default is unambiguous and
    project-local, where a hand-written absolute path is per-machine and
    easy to get wrong.

Usage::

    python docker/init_env.py             # seed .env, generate token if absent
    python docker/init_env.py --rotate-token
    python docker/init_env.py --show      # print the token, do not write

``.env`` is gitignored and ``.dockerignore`` excludes it, so nothing written
here is ever committed or baked into an image.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

# Path(__file__).resolve().parents[1] is the repository root, derived from this
# file's own location rather than the working directory, so the script does the
# right thing when invoked as `python docker/init_env.py` from anywhere
# (backend/core/config.py anchors the same way, for issue #34).
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# The default Syncthing folder, relative to the repository root. Matches the
# `SYNCTHING_ROOT` default in docker-compose.yaml and the `syncthing/` entry in
# .gitignore and .dockerignore. Change all three together.
DEFAULT_SYNCTHING_ROOT = "./syncthing"

# 32 bytes. token_urlsafe would also do, but hex is what the docs have always
# shown and it cannot produce a value that needs quoting in a .env file.
TOKEN_BYTES = 32

_ASSIGNMENT = re.compile(r"^(?P<prefix>#\s*)?(?P<key>[A-Z_][A-Z0-9_]*)\s*=(?P<value>.*)$")


def _is_commented(match: re.Match[str]) -> bool:
    return match.group("prefix") is not None


def _value_of(line: str) -> str | None:
    """The unquoted value of an assignment line, or None if it is not one."""
    match = _ASSIGNMENT.match(line)
    if match is None:
        return None
    return match.group("value").strip().strip("\"'")


def _set_key(lines: list[str], key: str, value: str) -> None:
    """Ensure ``key`` has an active, non-empty assignment in ``lines``.

    Prefers replacing an existing line in place - even a commented one - so the
    result stays as readable as the example it came from, instead of appending
    a second, contradictory copy at the end of the file.
    """
    for index, line in enumerate(lines):
        match = _ASSIGNMENT.match(line)
        if match is None or match.group("key") != key:
            continue
        # Already set and active: leave the operator's value alone.
        if not _is_commented(match) and _value_of(line):
            return
        lines[index] = f"{key}={value}"
        return

    lines.append(f"{key}={value}")


def _read_token(lines: list[str]) -> str | None:
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match and match.group("key") == "SHARED_TOKEN" and not _is_commented(match):
            value = _value_of(line)
            if value:
                return value
    return None


def _seed_from_example() -> bool:
    """Copy .env.example to .env if there is no .env yet. True if copied."""
    if ENV_FILE.exists():
        return False
    if not ENV_EXAMPLE.exists():
        print(f"error: {ENV_EXAMPLE} not found; run this from a complete checkout.", file=sys.stderr)
        raise SystemExit(1)
    ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="generate a new SHARED_TOKEN even if one is already set (logs every user out)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the current SHARED_TOKEN and exit without writing anything",
    )
    args = parser.parse_args(argv)

    if args.show:
        if not ENV_FILE.exists():
            print("no .env yet; run without --show first", file=sys.stderr)
            return 1
        token = _read_token(ENV_FILE.read_text(encoding="utf-8").splitlines())
        if not token:
            print("SHARED_TOKEN is not set in .env", file=sys.stderr)
            return 1
        print(token)
        return 0

    copied = _seed_from_example()
    if copied:
        print(f"created {ENV_FILE.name} from {ENV_EXAMPLE.name}")

    # newline="" on read, splitlines on the content: text mode would otherwise
    # translate CRLF and then write the file back with the platform's line
    # ending, churning every line of a file that may be CRLF on Windows.
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    generated: str | None = None
    existing = _read_token(lines)
    if existing and not args.rotate_token:
        print("SHARED_TOKEN is already set; leaving it alone")
    else:
        generated = secrets.token_hex(TOKEN_BYTES)
        reason = "rotated" if existing else "generated"
        print(f"{reason} SHARED_TOKEN ({TOKEN_BYTES * 2} hex characters)")

    _set_key(lines, "SHARED_TOKEN", generated or (existing or ""))
    _set_key(lines, "SYNCTHING_ROOT", DEFAULT_SYNCTHING_ROOT)

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Report the value that is ACTUALLY in force, not the default we just wrote.
    # If the operator had already pointed SYNCTHING_ROOT somewhere real, the
    # default is not what their cluster will use, and saying otherwise would
    # send them looking in a directory nothing is using.
    effective = next(
        (
            value
            for line in lines
            if (match := _ASSIGNMENT.match(line))
            and match.group("key") == "SYNCTHING_ROOT"
            and not _is_commented(match)
            and (value := _value_of(line))
        ),
        None,
    ) or DEFAULT_SYNCTHING_ROOT

    # Create the folder so the bind mount has something to mount and the
    # backend's mkdir of {jobs,nodes} lands in the real shared directory rather
    # than somewhere Docker invents. A relative value resolves against the
    # repository root, which is what compose does with it.
    candidate = Path(effective).expanduser()
    syncthing_dir = candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()
    try:
        syncthing_dir.mkdir(parents=True, exist_ok=True)
        location = str(syncthing_dir)
    except OSError as exc:
        # A configured path that does not exist and cannot be made - a network
        # share that is offline, say. Not fatal: the operator may have Syncthing
        # create it, and a hard failure here would block them from reaching the
        # token they need.
        location = f"{syncthing_dir} (not created: {exc})"

    print(f"SYNCTHING_ROOT={effective} -> {location}")
    print(f".env: {ENV_FILE}")
    print("next: docker compose up -d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())