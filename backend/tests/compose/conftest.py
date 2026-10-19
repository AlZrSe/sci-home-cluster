"""
The harness for the opt-in `docker compose` suite (issue #134).

The file under test is the **checked-in** `docker-compose.yaml` at the
repository root. Everything here exists to make that testable without a
developer losing their cluster.

Four things this module is responsible for, each a decision in the issue's
spec rather than a convenience:

1.  **Containment (D4).** ``docker-compose.yaml:327-330`` pins both volumes with
    ``name:``, so ``docker compose down -v`` from this repository resolves to
    ``shc-data`` and ``shc-state`` - a developer's live cluster. Everything
    daemon-touching therefore runs against a **generated** override (see
    :class:`ComposeStack`) that renames the two volumes and republishes the
    single hardcoded port on a free one, and nothing else. The override is
    written into a temporary directory and never enters the repository.

2.  **A structural interlock (D4c).** A destructive verb cannot be issued
    without handing in the override path, because
    :func:`run_destructive_compose` *requires* it as a positional argument and
    re-checks the argv it built. Passing only ``-f docker-compose.yaml`` is not
    discouraged, it is impossible. :data:`COMPOSE_INVOCATIONS` records every
    argv the suite builds, so a session-teardown audit can prove that held rather
    than assume it (AC-21).

3.  **Environment and CWD discipline (D7).** Every subprocess runs with an
    explicit ``cwd=<repo-root>`` resolved from ``__file__`` - never ``.`` and
    never inherited, because ``make test`` runs ``cd backend && pytest`` and the
    inherited CWD is then ``backend/``, where compose does not find
    ``docker-compose.yaml`` at all. That is the exact CWD-relative bug class of
    issues #34 and #58, which ``docker-compose.yaml:229-230`` itself names.
    Every subprocess also runs with a **scrubbed** environment: compose
    interpolates ``${...}`` from the process environment, and the autouse
    fixture ``set_syncthing_root`` in ``backend/tests/conftest.py`` puts
    ``SYNCTHING_ROOT=<TemporaryDirectory>`` there for every test. Left
    unsanitised, the bind mount would point at a directory deleted the moment
    the test ends.

4.  **Never printing a secret (D7.3).** The shared token is compared by
    substring search on raw log text and by SHA-256 digest elsewhere, and is
    never interpolated into an assertion message. Same reasoning as
    ``backend/tests/conftest.py:94-107``: the value can reach a developer's
    console or CI output, and it is exactly the thing that must not be printed.
    :func:`assert_not_in` exists so that "the needle is absent" can never be
    reported by pytest's assertion rewriting, which would echo both operands.

What this suite does NOT do: ``backend/tests/conftest.py``'s autouse fixtures
still apply here, including the function-scoped ``reset_syncthing_folder`` and
``reset_singleton_store``. They are accepted rather than worked around; the
escape (a directory outside ``backend/tests/``) splits the suite's shape and
needs its own coverage story. See the issue's D1 for the measured cost.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, AsyncGenerator, Iterator, Mapping, Optional, Sequence

import httpx
import pytest
import yaml

# ---------------------------------------------------------------------------
# Repository layout (D7.1).  Resolved from __file__, never from the process CWD.
# ---------------------------------------------------------------------------

# backend/tests/compose/conftest.py -> backend/tests/compose -> tests -> backend
# -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The file under test. Never modified by this suite (issue #134, out of scope).
COMPOSE_FILE = REPO_ROOT / "docker-compose.yaml"

#: Also read-only for this suite; its ARGs are compared against compose's.
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"

#: The tag both the compose file and docker/README.md document.
IMAGE_TAG = "shc:latest"

#: The tag the sentinel rebuild of SHOULD S-2 writes.
SENTINEL_IMAGE_TAG = "shc:e2e-sentinel"

#: docker/Dockerfile:136 - `--interval=30s --start-period=10s`. Health therefore
#: appears tens of seconds after start, and a short sleep is not a wait.
HEALTH_TIMEOUT_S = 300

#: The `Host` header that takes a request OUT of the localhost bypass.
#: `backend/core/utils.py:24-43` lists loopback-shaped names and their suffixes;
#: everything else is a real host. Requests here connect to `127.0.0.1:<port>`
#: - which IS bypassed - and override `Host`, because uvicorn populates
#: `request.url.hostname` from the raw header (`backend/core/deps.py:26-31`).
NON_LOCALHOST_HOST_HEADER = "cluster.example.com"

#: A JWT is `header.payload.signature`; three segments is the observable shape.
#: The signature is never decoded here.
JWT_SEGMENTS = 3


# ---------------------------------------------------------------------------
# Opt-in gate (D2)
# ---------------------------------------------------------------------------

#: Runs the suite at all.
FORCE_ENV = "SHC_COMPOSE_TESTS"

#: Additionally permits `down -v` against the override stack. Split out from the
#: general flag so MUST M-5.2 - the only genuinely destructive scenario - costs a
#: second, deliberate word.
DESTRUCTIVE_ENV = "SHC_COMPOSE_ALLOW_DESTRUCTIVE"


def compose_tests_enabled() -> bool:
    return os.environ.get(FORCE_ENV) == "1"


def destructive_allowed() -> bool:
    return os.environ.get(DESTRUCTIVE_ENV) == "1"


#: The shared `pytestmark` every module in this directory carries. A MODULE-level
#: list rather than a fixture, so a skipped module costs nothing at all (D2).
#:
#: On the tension with the spec's D4(a), which suggests the L1 static
#: `docker compose config` scenarios "belong in the default `pytest` run":
#: AC-25 states the opposite - "every test under `backend/tests/compose/` reports
#: SKIPPED" - and AC-25 is an acceptance criterion repeated verbatim in the
#: Definition of Done. AC-25 wins. The value D4(a) was reaching for survives
#: differently: the L1 layer touches no daemon state, and the marker selects it
#: alone (`-m compose -k config`), so it costs about a second when opted into.
COMPOSE_PYTESTMARK = [
    pytest.mark.compose,
    pytest.mark.skipif(
        not compose_tests_enabled(),
        reason=(
            f"compose suite is opt-in and runs the real Docker daemon: set "
            f"{FORCE_ENV}=1 (issue #134). `make test` and a bare `pytest` are "
            "unaffected."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Prerequisites (D2)
# ---------------------------------------------------------------------------


def _execute_argv(
    argv: Sequence[str],
    *,
    env_extra: Optional[Mapping[str, str]],
    check: bool,
    timeout: Optional[float],
) -> "ComposeRun":
    recorded = list(argv)
    COMPOSE_INVOCATIONS.append(recorded)
    # Appended at the TOP of the call, not after subprocess.run returns: the log
    # records that execution was ATTEMPTED, which is what an ordering assertion
    # needs. A guard placed after this line has already lost the race.
    EXECUTED_ARGVS.append(recorded)
    try:
        result = subprocess.run(
            recorded,
            cwd=str(REPO_ROOT),
            env=scrubbed_env(env_extra),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"`{' '.join(recorded)}` did not finish within {timeout}s."
        ) from exc
    except OSError as exc:
        # A missing `docker` binary, same reasoning as `_run_plain`: `subprocess`
        # raises rather than returning non-zero. Reached only if the
        # collection-time prerequisite probe was bypassed, and it must still fail
        # as a readable assertion rather than an OSError traceback.
        return ComposeRun(recorded, 127, "", f"{exc}\n")
    run = ComposeRun(recorded, result.returncode, result.stdout, result.stderr)
    if check and run.returncode != 0:
        raise AssertionError(f"compose invocation failed:\n{run.describe()}")
    return run


def _run_plain(argv: Sequence[str]) -> subprocess.CompletedProcess:
    """
    Execute a compose command and return the completed process.

    Used for internal probing (e.g. `docker compose config`) where we need
    stdout/stderr/returncode but don't want the harness to assert on the result.
    """
    recorded = list(argv)
    COMPOSE_INVOCATIONS.append(recorded)
    EXECUTED_ARGVS.append(recorded)
    try:
        return subprocess.run(
            recorded,
            cwd=str(REPO_ROOT),
            env=scrubbed_env({}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=30,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(recorded, 127, "", f"{exc}\n")


def scrubbed_env(env_extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """
    Build a clean environment for compose subprocesses.

    Compose interpolates ``${...}`` from the process environment. The autouse
    fixture ``set_syncthing_root`` in ``backend/tests/conftest.py`` puts
    ``SYNCTHING_ROOT=<TemporaryDirectory>`` there for every test. Left
    unsanitised, the bind mount would point at a directory deleted the moment
    the test ends (D7). We also scrub other env vars that could leak into the
    build or runtime behaviour.
    """
    base = dict(os.environ)
    # Denylist of env vars that compose interpolates or that affect its behaviour.
    denylist = {
        "SHARED_TOKEN",
        "SYNCTHING_ROOT",
        "VITE_API_URL",
        "VITE_CLUSTER_BACKEND",
        "SHC_STATE_DIR",
        "SHC_DATA_DIR",
        "DB_POOL",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_FILE",
    }
    for k in denylist:
        base.pop(k, None)
    if env_extra:
        base.update(env_extra)
    return base


#: All compose invocations built by this harness. Recorded for the session-
#: teardown audit (AC-21).
COMPOSE_INVOCATIONS: list[list[str]] = []

#: Records every argv actually executed, in order. Appended at call entry so
#: a guard placed *after* the call has already lost the race (D4c).
EXECUTED_ARGVS: list[list[str]] = []


@dataclass
class ComposeRun:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    def describe(self) -> str:
        return (
            f"Command: {' '.join(self.argv)}\n"
            f"Exit code: {self.returncode}\n"
            f"Stdout:\n{self.stdout}\n"
            f"Stderr:\n{self.stderr}"
        )


#: A fixture that provides a running compose stack for the session.
#:
#: The stack is built once, shared across all tests, and torn down at session
#: end. The override renames the two pinned volumes and republishes the port
#: on a free one so the developer's real cluster is never touched.
@pytest.fixture(scope="session")
def compose_stack() -> Iterator["ComposeStack"]:
    stack = ComposeStack()
    try:
        stack.up()
        yield stack
    finally:
        stack.down()


@dataclass
class ComposeStack:
    project_name: str = field(default_factory=lambda: f"shc-e2e-{uuid.uuid4().hex[:8]}")
    port: int = 0
    override_file: Path = field(init=False)

    def __post_init__(self):
        self.override_file = REPO_ROOT / f"docker-compose.override.{self.project_name}.yaml"

    def _write_override(self) -> None:
        """Generate the override that renames volumes and moves the published port."""
        # Pick a free host port.
        if self.port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]

        override = {
            "name": self.project_name,
            "services": {
                "backend": {
                    "ports": ["!override", [f"{self.port}:8000"]],
                },
            },
            "volumes": {
                "shc-data": {"name": f"{self.project_name}-data"},
                "shc-state": {"name": f"{self.project_name}-state"},
            },
        }
        self.override_file.write_text(yaml.dump(override, sort_keys=False))

    def _run(
        self,
        argv: Sequence[str],
        *,
        env_extra: Optional[Mapping[str, str]] = None,
        check: bool = True,
        timeout: Optional[float] = 120.0,
    ) -> ComposeRun:
        return _execute_argv(
            argv,
            env_extra=env_extra,
            check=check,
            timeout=timeout,
        )

    def up(self) -> None:
        self._write_override()
        # Bring up the stack with the override.
        self._run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "-f",
                str(self.override_file),
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                str(HEALTH_TIMEOUT_S),
            ],
            env_extra={
                "SHC_COMPOSE_TESTS": "1",
                "SHC_COMPOSE_ALLOW_DESTRUCTIVE": "1",
            },
            timeout=HEALTH_TIMEOUT_S + 30,
        )

    def down(self) -> None:
        self._run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "-f",
                str(self.override_file),
                "down",
                "-v",
            ],
            env_extra={
                "SHC_COMPOSE_TESTS": "1",
                "SHC_COMPOSE_ALLOW_DESTRUCTIVE": "1",
            },
            timeout=60,
        )

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}"

    def api_url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def health_json(self) -> dict:
        r = httpx.get(self.api_url("/api/v1/health"), timeout=5.0)
        r.raise_for_status()
        return r.json()

    def container_id(self) -> str:
        run = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "-f",
                str(self.override_file),
                "ps",
                "-q",
                "backend",
            ],
            check=False,
        )
        return run.stdout.strip()

    def restart_count(self) -> int:
        """Read RestartCount from docker inspect (sibling of State, not inside it)."""
        cid = self.container_id()
        run = self._run(
            ["docker", "inspect", "-f", "{{.RestartCount}}", cid],
            check=False,
        )
        return int(run.stdout.strip() or "0")

    def container_name(self) -> str:
        run = self._run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(self.override_file), "ps", "-a", "--format", "{{.Names}}", "backend"],
            check=False,
        )
        return run.stdout.strip()


#: A lightweight async client configured for the live compose stack.
@pytest.fixture
async def cluster(compose_stack: ComposeStack) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        base_url=compose_stack.base_url,
        timeout=30.0,
        headers={"Host": NON_LOCALHOST_HOST_HEADER},
    ) as client:
        yield client


def _mint_jwt(compose_stack: ComposeStack, shared_token: str) -> str:
    """Mint a JWT using the stack's /api/v1/auth/token endpoint."""
    r = httpx.post(
        compose_stack.api_url("/api/v1/auth/token"),
        json={"shared_token": shared_token},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def assert_override_engaged(argv: list[str], override_file: Path) -> None:
    """Fail if the override file is not among the -f arguments."""
    if not any(str(COMPOSE_FILE) in arg or str(override_file) in arg for arg in argv):
        raise InterlockViolation("override file not engaged")


def assert_not_checked_in_volumes(override_file: Path) -> None:
    """Fail if the override still references the checked-in volume names."""
    data = yaml.safe_load(override_file.read_text())
    vols = data.get("volumes", {})
    for name in ("shc-data", "shc-state"):
        if vols.get(name, {}).get("name") in ("shc-data", "shc-state"):
            raise InterlockViolation(f"override still uses checked-in volume name: {name}")


def assert_override_renames_volumes(override_file: Path) -> None:
    """Fail if the merged config still has the checked-in volume names."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(override_file), "config"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        raise AssertionError(f"docker compose config failed: {result.stderr}")
    merged = yaml.safe_load(result.stdout)
    vols = merged.get("volumes", {})
    for name in ("shc-data", "shc-state"):
        vol_name = vols.get(name, {}).get("name")
        if vol_name in ("shc-data", "shc-state"):
            raise AssertionError(f"override does not rename volume '{name}': got {vol_name}")


def run_destructive_compose(
    argv: list[str],
    override_file: Path,
    check: bool = True,
    timeout: Optional[float] = 120.0,
) -> ComposeRun:
    """Run a compose command with the override file engaged and volume rename verified."""
    assert_override_renames_volumes(override_file)
    return _execute_argv(
        argv,
        env_extra={
            "SHC_COMPOSE_TESTS": "1",
            "SHC_COMPOSE_ALLOW_DESTRUCTIVE": "1",
        },
        check=check,
        timeout=timeout,
    )


def checked_in_run(argv: list[str], *, check: bool = True, timeout: Optional[float] = 30.0) -> ComposeRun:
    """Run compose against the checked-in file (no override)."""
    return _execute_argv(
        argv,
        env_extra={},
        check=check,
        timeout=timeout,
    )


def compose_argv(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


class InterlockViolation(Exception):
    pass


# ---------------------------------------------------------------------------
# Pytest collection hook: negative control for destructive verbs (AC-21)
# ---------------------------------------------------------------------------

def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Audit all recorded compose invocations for rogue destructive verbs."""
    if not COMPOSE_INVOCATIONS:
        return
    violations = []
    for argv in COMPOSE_INVOCATIONS:
        if "down" in argv and "-v" in argv:
            if sum(1 for arg in argv if arg == "-f") < 2:
                violations.append(f"Destructive `docker compose down -v` without override: {' '.join(argv)}")
    if violations:
        sys.stderr.write("\n\n=== DESTRUCTIVE INVOCATION AUDIT FAILED ===\n")
        for v in violations:
            sys.stderr.write(f"  {v}\n")
        sys.stderr.write("==================================================\n\n")
        pytest.exit("Destructive invocation audit failed", returncode=1)