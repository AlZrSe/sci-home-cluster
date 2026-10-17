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
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import httpx
import pytest

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
            "compose suite is opt-in and runs the real Docker daemon: set "
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
    run = ComposeRun(recorded, result.returncode, result.stdout, result.stderr)
    if check and run.returncode != 0:
        raise AssertionError(f"compose invocation failed:\n{run.describe()}")
    return run


def _run_plain(argv: Sequence[str]) -> subprocess.CompletedProcess:
    """A plain `docker ...` call. Explicit cwd, inherited-but-scrubbed env."""
    return subprocess.run(
        list(argv),
        cwd=str(REPO_ROOT),
        env=scrubbed_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )


def compose_cli_version() -> Optional[str]:
    """`docker compose version`, or None when the CLI cannot report one."""
    result = _run_plain(["docker", "compose", "version"])
    return result.stdout.strip() if result.returncode == 0 else None


def docker_daemon_version() -> Optional[str]:
    """A zero-cost daemon reachability check.

    `docker version --format {{.Server.Version}}` succeeds only when a daemon
    answers. Deliberately NOT a `docker compose up` attempt: probing by starting
    something is how a probe becomes a side effect.
    """
    result = _run_plain(["docker", "version", "--format", "{{.Server.Version}}"])
    return result.stdout.strip() if result.returncode == 0 else None


def missing_prerequisite() -> Optional[str]:
    """A message naming what is missing, or None when the suite can run."""
    if compose_cli_version() is None:
        return (
            "the Docker Compose CLI is unavailable: `docker compose version` "
            "failed. The suite needs `docker compose` on PATH to parse the "
            "checked-in file."
        )
    if docker_daemon_version() is None:
        return (
            "no reachable Docker daemon: `docker version` reported no server. "
            "Start Docker Desktop (or the daemon) and re-run."
        )
    return None


def pytest_collection_modifyitems(config, items) -> None:
    """
    Give ``SHC_COMPOSE_TESTS=1`` without Docker a message naming the MISSING
    PREREQUISITE, distinguishable from "you did not opt in" (D2).

    A module-level ``skipif`` cannot express this: it is evaluated at import time
    and cannot run a subprocess. The two markers stack in pytest, so the opt-in
    marker in :data:`COMPOSE_PYTESTMARK` still applies and this one adds a
    second, different reason.
    """
    reason = missing_prerequisite()
    if reason is None:
        return
    skip = pytest.mark.skip(
        reason=f"{FORCE_ENV}=1 was set but {reason} Issue #134, AC-25."
    )
    for item in items:
        if "compose" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------------------
# Scrubbed environment (D7.2)
# ---------------------------------------------------------------------------

#: Everything that could reach a `${...}` interpolation in docker-compose.yaml or
#: a build ARG, and that this suite sets deliberately rather than inherits.
#:
#: `set_syncthing_root` (backend/tests/conftest.py:419-436) injects
#: SYNCTHING_ROOT=<TemporaryDirectory> into ``os.environ`` for EVERY test, and
#: compose interpolates ``${SYNCTHING_ROOT:-./syncthing}`` from the process
#: environment. Without the scrub the bind source silently becomes a directory
#: that is deleted the moment the test ends, and MUST M-4.2 - the only assertion
#: in the suite that distinguishes "the bind mount works" from "#104's silent
#: second cluster" - would pass or fail against the wrong path.
#:
#: COMPOSE_PROJECT_NAME is denylisted because it is the tempting alternative to
#: renaming the volumes in the override. MEASURED, it does NOT namespace them
#: (`volumes.shc-data.name` stays `shc-data`), so relying on it would hand the
#: real blast radius straight back.
ENV_DENYLIST = frozenset(
    {
        "SHARED_TOKEN",
        "SYNCTHING_ROOT",
        "VITE_API_URL",
        "VITE_CLUSTER_BACKEND",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_FILE",
        FORCE_ENV,
        DESTRUCTIVE_ENV,
    }
)

#: Prefixes scrubbed wholesale. ``SHC_*`` is the backend's own settings namespace
#: (SHC_DATA_DIR, SHC_STATE_DIR); the image sets those and compose must not be
#: influenced by a developer's shell.
ENV_DENY_PREFIXES = ("SHC_",)


def scrubbed_env(extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """A subprocess environment with the denylist removed.

    ``extra`` is the ONLY way a scenario injects a value, so an injected value is
    always visible at the call site that needed it.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ENV_DENYLIST and not key.startswith(ENV_DENY_PREFIXES)
    }
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# The destructive-verb interlock (D4c)
# ---------------------------------------------------------------------------


class InterlockViolation(AssertionError):
    """A destructive verb was aimed at the checked-in file's own state."""


#: Every `docker compose` argv this suite builds, in order. Audited at session
#: teardown (AC-21's second half) rather than asserted once inside a test,
#: because pytest gives no cross-module ordering guarantee: a single test could
#: run before the lifecycle module that issues the `down -v`.
COMPOSE_INVOCATIONS: list[list[str]] = []


def compose_argv(override_path: Optional[Path], *args: str) -> list[str]:
    """The argv for a compose call, always naming both files when overridden.

    With an override: ``docker compose -f docker-compose.yaml -f <override> ...``,
    which is exactly how an operator's own ``docker compose up`` reads the
    checked-in bytes. Without one: the single-file form, which is safe only for
    read-only verbs - see :func:`run_destructive_compose`.
    """
    argv = ["docker", "compose"]
    if override_path is not None:
        argv += ["-f", str(COMPOSE_FILE), "-f", str(Path(override_path))]
    argv += list(args)
    return argv


def assert_override_engaged(override_path: Path, argv: Sequence[str]) -> None:
    """Refuse argv whose ``-f`` flags do not carry the override.

    Split out from :func:`run_destructive_compose` as a pure function so a test
    can prove the refusal WITHOUT executing ``docker`` - which is the only way to
    test an interlock whose failure mode is deleting a developer's database.
    """
    if Path(override_path) == COMPOSE_FILE:
        raise InterlockViolation(
            "refusing a destructive verb: the override path handed in IS the "
            f"checked-in {COMPOSE_FILE.name}. That is the file whose volumes are "
            "named shc-data and shc-state (docker-compose.yaml:327-330), so a "
            "`down -v` aimed at it deletes a developer's live cluster."
        )
    file_flags = [argv[i + 1] for i, token in enumerate(argv) if token == "-f"]
    if str(override_path) not in file_flags:
        raise InterlockViolation(
            f"refusing a destructive verb: the override path {override_path} is "
            f"not among the -f flags {file_flags}. A `down -v` without the "
            "override resolves to the checked-in file's own volumes."
        )
    if str(COMPOSE_FILE) not in file_flags:
        raise InterlockViolation(
            "refusing a destructive verb: the checked-in docker-compose.yaml is "
            f"not among the -f flags {file_flags}. Without it there is no file "
            "under test, only the override."
        )


@dataclass
class ComposeRun:
    """A finished compose subprocess, with the argv that produced it."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """stdout and stderr together, for a combined-content assertion."""
        return self.stdout + self.stderr

    def describe(self) -> str:
        return (
            f"$ {' '.join(self.argv)}\nexit={self.returncode}\n"
            f"--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"
        )


def run_compose(
    argv: Sequence[str],
    *,
    env_extra: Optional[Mapping[str, str]] = None,
    check: bool = True,
    timeout: Optional[float] = None,
) -> ComposeRun:
    """Run a NON-destructive compose argv built by :func:`compose_argv`."""
    return _execute_argv(argv, env_extra=env_extra, check=check, timeout=timeout)


def run_destructive_compose(
    override_path: Path,
    *args: str,
    env_extra: Optional[Mapping[str, str]] = None,
    check: bool = True,
    timeout: Optional[float] = None,
) -> ComposeRun:
    """Run a verb that can destroy daemon state, against the override stack.

    **The override path is a required positional argument.** That is the whole
    interlock (D4c): there is no call shape in which ``-f docker-compose.yaml``
    alone gets a ``down -v`` through, because the argv is built by
    :func:`compose_argv` with the override mandatory and then re-validated by
    :func:`assert_override_engaged`. A review comment is a rule somebody can route
    around; this is not reachable.

    ``SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`` is required as well, so MUST M-5.2 - the
    only genuinely destructive scenario - costs a second, deliberate word (D2). It
    is enforced HERE rather than in the test, so no caller can forget it.
    """
    argv = compose_argv(override_path, *args)
    assert_override_engaged(override_path, argv)
    if not destructive_allowed():
        pytest.skip(
            "MUST M-5.2 issues `docker compose down -v`. Re-run with "
            f"{DESTRUCTIVE_ENV}=1 to permit deleting the throwaway shc-e2e-* "
            "volumes this suite created. Those are NOT the checked-in file's "
            "shc-data / shc-state, which this suite never touches."
        )
    return _execute_argv(argv, env_extra=env_extra, check=check, timeout=timeout)


def checked_in_run(
    *args: str,
    env_extra: Optional[Mapping[str, str]] = None,
    check: bool = True,
) -> ComposeRun:
    """A read-only verb against the SINGLE checked-in file.

    Only ``config`` and the ``--services``-style parse verbs belong here: they
    create and remove no container, network or volume, so they are safe with no
    override and MUST be. The single-file resolution is also the only view in
    which ``published == "8000"`` and ``volumes.shc-data.name == "shc-data"``
    are visible at all (AC-5, AC-8/M-1.8).
    """
    return _execute_argv(
        compose_argv(None, *args), env_extra=env_extra, check=check, timeout=None
    )


def parse_config(run: ComposeRun) -> dict[str, Any]:
    """The JSON document behind a ``config --format json`` invocation."""
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - a CLI change
        raise AssertionError(
            "`docker compose config --format json` did not return JSON: "
            f"{exc}\n{run.describe()}"
        ) from exc


# ---------------------------------------------------------------------------
# Ports, volumes, digests (D5, D4c.2, D7.3)
# ---------------------------------------------------------------------------


def free_port() -> int:
    """An OS-assigned free TCP port: bind ``:0``, then let go.

    Chosen dynamically rather than hardcoded (D5). The checked-in file pins
    ``8000:8000`` by decision - ``docker-compose.yaml:112-118`` - so the suite
    cannot parameterise the port through the file under test, which is exactly
    why the override exists. Picking dynamically removes the collision class
    rather than dodging one instance of it.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_holder(port: int) -> str:
    """Best-effort name of whatever listens on ``port``, for a skip message.

    D5 asks for a collision to be reported as "this port, held by this process"
    rather than as a compose bind error that reads like a compose bug.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a runtime dependency
        return "an unknown process (psutil unavailable)"
    try:
        for conn in psutil.net_connections(kind="inet"):
            laddr = conn.laddr
            if not laddr or laddr.port != port or conn.status != psutil.CONN_LISTEN:
                continue
            try:
                return psutil.Process(conn.pid).name()
            except Exception:  # pragma: no cover - the process may have gone
                return f"pid {conn.pid}"
    except Exception:  # pragma: no cover - permissions vary by platform
        pass
    return "an unknown process"


def volume_exists(name: str) -> bool:
    """Whether a volume of this EXACT name exists."""
    return _run_plain(["docker", "volume", "inspect", name]).returncode == 0


def run_in_image(image: str, script: str) -> str:
    """Run a POSIX shell script in a throwaway, NEVER-STARTED container.

    Used by the L2 scenarios. `--rm` also drops the anonymous volumes the image
    declares, so this leaves nothing behind - and, importantly, never starts a
    server, never publishes a port and never writes cluster state.

    The script is expected to exit 0 and to report through marker lines on
    stdout, so a failing assertion names what was actually in the image instead
    of just a non-zero exit code.

    Note the output can be megabytes (MUST M-2.4 concatenates every `.js` file),
    which is why the shared subprocess runner pins `encoding="utf-8"` rather than
    inheriting the host locale: the bundle is UTF-8, and a Windows console code
    page such as cp1251 raises UnicodeDecodeError inside the reader thread.
    """
    run = run_compose(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script],
        check=True,
    )
    return run.stdout


def image_config(image: str) -> dict[str, Any]:
    """`docker image inspect <tag>` -> `.Config`, as a dict."""
    result = _run_plain(["docker", "image", "inspect", image])
    assert (
        result.returncode == 0
    ), f"docker image inspect {image} failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)[0]["Config"]


def digest(value: str) -> str:
    """SHA-256 of a secret, never the secret itself.

    Same reasoning as ``backend/tests/conftest.py:94-107``.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def assert_not_in(needle: str, haystack: str, message: str) -> None:
    """Fail when ``needle`` occurs in ``haystack``, without echoing either.

    ``assert needle not in haystack`` would have pytest's assertion rewriting
    render the explanation - which includes both operands - so a "the token is
    not in the logs" test would print the token on failure. That is precisely the
    leak this suite exists to avoid, so the check is done by hand.
    """
    if needle in haystack:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# The stack
# ---------------------------------------------------------------------------


@dataclass
class ComposeStack:
    """One long-lived stack behind a generated override (D3, D4b, D6).

    Built once per session: ``docker compose build`` of this image runs ``npm ci``,
    a lockfile platform check and a full vite build (``docker/Dockerfile:49``,
    ``:70-79``), so per-test ``up`` would take hours and rebuild N-1 times for
    nothing.

    The override changes exactly two things, both unsafe to share on a developer
    machine, and nothing else: the two pinned volume NAMES (the blast radius,
    D4c) and the single hardcoded PUBLISHED PORT (a constant by decision, so it
    cannot be parameterised through the file under test, D5). Build args,
    environment keys, mounts, restart policy and image are still the checked-in
    file's, and MUST M-1.* asserts them against the single-file resolution
    regardless (D6).
    """

    tag: str
    port: int
    override_path: Path
    workdir: Path
    data_volume: str = ""
    state_volume: str = ""
    #: `docker compose logs backend` captured IMMEDIATELY after the first `up`,
    #: before any test can run. MUST M-3.7 is only meaningful on a first start,
    #: and a lifecycle scenario may already have recreated the container by the
    #: time the L3 module runs - so the evidence is captured structurally, at
    #: fixture time, rather than relying on test order (D3).
    first_start_logs: str = ""
    #: The token as generated on that first start, kept so M-3.7 can compare the
    #: value against the log text without either being printed.
    first_start_token: str = ""
    #: Every job id this session submitted, so teardown removes exactly those
    #: directories and nothing else (D4d).
    submitted_jobs: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.data_volume = f"{self.tag}-data"
        self.state_volume = f"{self.tag}-state"

    # -- naming ------------------------------------------------------------
    @property
    def project_name(self) -> str:
        return self.tag

    @property
    def volume_names(self) -> tuple[str, str]:
        """The two volumes this stack owns, by EXACT name.

        Everything destructive in the suite is addressed through here - never by
        glob, project name or label (D4c.4).
        """
        return (self.data_volume, self.state_volume)

    def assert_not_checked_in_volumes(self) -> None:
        """The containment invariant, re-checked before anything destructive."""
        for name in ("shc-data", "shc-state"):
            if name in self.volume_names:
                raise InterlockViolation(
                    f"refusing to act: {name} is one of this stack's volume "
                    "names, and that is the checked-in file's state "
                    "(docker-compose.yaml:327-330)."
                )

    # -- invocation --------------------------------------------------------
    def compose(self, *args: str, check: bool = True, **kwargs: Any) -> ComposeRun:
        """A compose verb against the override stack."""
        return run_compose(
            compose_argv(self.override_path, *args),
            env_extra=kwargs.pop("env_extra", None),
            check=check,
            timeout=kwargs.pop("timeout", None),
        )

    def destructive(self, *args: str, check: bool = True, **kwargs: Any) -> ComposeRun:
        """``down -v`` and friends. See :func:`run_destructive_compose`."""
        self.assert_not_checked_in_volumes()
        return run_destructive_compose(self.override_path, *args, check=check, **kwargs)

    # -- lifecycle ---------------------------------------------------------
    def up(self, *extra: str, timeout: int = HEALTH_TIMEOUT_S) -> ComposeRun:
        """``up -d --wait``, which waits on the image's own HEALTHCHECK.

        ``--wait`` / ``--wait-timeout`` confirmed present in Compose v5.6.0.
        :meth:`wait_healthy` is the fallback for the case where ``--wait`` is
        unavailable or exits early.
        """
        self.assert_not_checked_in_volumes()
        return self.compose(
            "up", "-d", "--wait", "--wait-timeout", str(timeout), *extra
        )

    def down(self) -> ComposeRun:
        """``down`` WITHOUT ``-v``: removes containers, keeps both volumes.

        This is the documented behaviour (``docker-compose.yaml:321-325``) and
        MUST M-5.1 is its test, so the suite must be able to perform it.
        """
        return self.compose("down")

    def restart(self, service: str = "backend") -> ComposeRun:
        return self.compose("restart", service)

    # -- inspection --------------------------------------------------------
    def container_id(self) -> str:
        ids = self.compose("ps", "-q", "backend").stdout.split()
        assert ids, "no container for service `backend`; is the stack up?"
        return ids[0]

    def inspect(self, template: str, cid: Optional[str] = None) -> Any:
        cid = cid or self.container_id()
        result = _run_plain(["docker", "inspect", cid, "--format", template])
        assert (
            result.returncode == 0
        ), f"docker inspect {cid} failed:\n{result.stdout}\n{result.stderr}"
        return json.loads(result.stdout.strip())

    def health_status(self, cid: Optional[str] = None) -> Optional[str]:
        try:
            state = self.inspect("{{json .State}}", cid)
        except (AssertionError, IndexError):
            return None
        return (state.get("Health") or {}).get("Status")

    def wait_healthy(
        self, timeout: int = HEALTH_TIMEOUT_S, cid: Optional[str] = None
    ) -> str:
        """Poll ``.State.Health.Status`` until it reads ``healthy``.

        Not a sleep: the inherited HEALTHCHECK is ``--interval=30s
        --start-period=10s``, so health legitimately arrives tens of seconds
        after start and a naive short sleep is not a wait.
        """
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        while time.monotonic() < deadline:
            status = self.health_status(cid)
            if status is not None and status not in seen:
                seen.append(status)
            if status == "healthy":
                return status
            time.sleep(1.0)
        raise AssertionError(
            f"container {cid or '<current>'} did not become healthy within "
            f"{timeout}s; statuses observed: {seen or ['<none>']}. The image's "
            "own HEALTHCHECK (docker/Dockerfile:136-137) is what is under test, "
            "so `docker compose logs backend` is the next step."
        )

    def logs(self, service: str = "backend") -> str:
        """Raw log text for a service, stderr included: uvicorn writes there."""
        return self.compose("logs", service).output

    def exec(self, *argv: str, check: bool = True) -> ComposeRun:
        """``compose exec -T``: no TTY, because the pipes are not one."""
        return self.compose("exec", "-T", "backend", *argv, check=check)

    # -- state this suite owns ---------------------------------------------
    def resolved_config(self) -> dict[str, Any]:
        return parse_config(self.compose("config", "--format", "json"))

    def bind_source(self) -> Path:
        """The host directory the Syncthing bind mount points at.

        Read from the OVERRIDE stack's own resolved config and never hardcoded
        (AC-18). Hardcoding ``<repo-root>/syncthing`` would let the test pass
        against a directory the running stack is not watching - which is the
        exact defect MUST M-4.2 exists to catch.
        """
        for mount in self.resolved_config()["services"]["backend"]["volumes"]:
            if mount.get("target") == "/tmp/syncthing":
                return Path(mount["source"])
        raise AssertionError(
            "no bind mount with target /tmp/syncthing in the resolved config"
        )

    def shared_token(self) -> str:
        """The token at ``/app/.shc/shared_token``, read inside the container.

        The value never leaves this method except into a digest or a substring
        search. See the module docstring.
        """
        return self.exec("cat", "/app/.shc/shared_token").stdout.strip()

    def token_digest(self) -> str:
        return digest(self.shared_token())

    # -- the Syncthing folder ---------------------------------------------
    def job_state_file(self, job_id: str) -> Path:
        """``<bind-source>/jobs/<job_id>/state.yaml``, as the HOST sees it.

        The bind source is read from the resolved config, never hardcoded
        (AC-18).
        """
        return self.bind_source() / "jobs" / job_id / "state.yaml"

    def record_job(self, job_id: str) -> None:
        """Note a job this session submitted, so teardown can remove exactly it.

        D4(d): the bind mount deliberately still points at
        ``<repo-root>/syncthing``, because pointing it elsewhere would mean not
        testing the documented default. The consequence is that the suite writes
        operator-shaped paths, and it therefore owns its own cleanup - and owns
        ONLY its own cleanup. ``reset_syncthing_folder`` in the root conftest
        empties the session temporary root and provides no protection here at
        all.
        """
        self.submitted_jobs.add(job_id)

    def preflight_bind_root(self) -> None:
        """
        Refuse to run if the bind folder already holds job directories.

        ``JobService.submit_job`` has a create-time clobber guard
        (``backend/services/job_service.py:86-91``): a directory already at
        ``<root>/jobs/<id>`` means the database counter and the folder disagree,
        and the request is answered 409 instead of submitted. Two reasons this
        suite must preflight rather than discover:

        * a leftover from an interrupted earlier run would make the suite fail
          with a 409 that reads like an application bug;
        * a developer's OWN cluster state sitting there is not the suite's to
          delete, and clearing the folder wholesale is exactly what D4(d)
          forbids.

        So it skips, naming what it found. Deliberately a skip, not a cleanup.
        """
        jobs_root = self.bind_source() / "jobs"
        if not jobs_root.is_dir():
            return
        existing = sorted(
            entry.name
            for entry in jobs_root.iterdir()
            if entry.is_dir() and entry.name.startswith("job-")
        )
        if existing:
            pytest.skip(
                f"the Syncthing bind folder already holds job directories: "
                f"{existing} (under {jobs_root}). This suite writes there by "
                "design - the documented default bind mount is part of what is "
                "under test - and it removes only the directories IT created, so "
                "a leftover from an interrupted earlier run (or your own cluster "
                "state) would collide with it. Move or remove them and re-run."
            )


# ---------------------------------------------------------------------------
# Job submission over the published port (L4)
# ---------------------------------------------------------------------------

#: A minimal but VALID JobSpec (`shared/schemas/job_spec.py`). The command is
#: never executed - there is no agent in this compose file by decision
#: (`docker-compose.yaml:36-70`) - so what matters is only that the backend
#: accepts it and publishes it.
JOB_SPEC = {
    "name": "compose-e2e",
    "command": "echo compose-e2e",
    "working_dir": "/tmp/compose-e2e",
    "env": {"SHC_COMPOSE_E2E": "1"},
    "resources": {"gpus": 0, "cpus": 1, "memory_gb": 1},
    "paths": {"input": "/tmp/compose-e2e/in", "output": "/tmp/compose-e2e/out"},
}


def submit_job(stack: ComposeStack, token: str = "") -> str:
    """`POST /api/v1/jobs/` over the published port -> the new job id.

    Protects `backend/services/job_service.py:58-103` and the router at
    `backend/api/v1/jobs.py:106-115`.

    **The trailing slash is the contract.** `openapi.yaml:187` declares
    `/api/v1/jobs/`, because the route is declared `@router.post("/")`
    (`jobs.py:106`). Asking for `/api/v1/jobs` without it answers **405**, not
    the 307 slash-redirect you might expect: the SPA catch-all
    `@app.get("/{full_path:path}")` in `backend/core/frontend.py:125` is a
    partial match for that path, and Starlette reports a partial match as 405
    before it ever reaches the redirect logic. Measured, not inferred - and worth
    knowing, because the symptom reads like "the API rejected my job".

    The job is recorded on the stack so teardown removes exactly this directory
    and nothing else.
    """
    jwt = token or _mint_jwt(stack)
    with ClusterClient(stack.port) as instance:
        response = instance.post(
            "/api/v1/jobs/",
            json=JOB_SPEC,
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert response.status_code == 201, (
        f"POST /api/v1/jobs/ answered {response.status_code}: {response.text}. "
        "A 409 here is the create-time clobber guard "
        "(backend/services/job_service.py:78-91), meaning the database and the "
        "Syncthing folder disagree about a job id; a 405 means the trailing "
        "slash is missing (openapi.yaml:187)."
    )
    job_id = response.json()["job_id"]
    stack.record_job(job_id)
    return job_id


# ---------------------------------------------------------------------------
# Live-container HTTP (L3)
# ---------------------------------------------------------------------------


class ClusterClient:
    """A synchronous HTTP client aimed at the published port.

    Every request goes to ``127.0.0.1``, which IS in the localhost bypass list.
    Tests that care about the non-bypassed path override ``Host``, because
    uvicorn populates ``request.url.hostname`` from the raw header
    (``backend/core/deps.py:26-31``).
    """

    def __init__(self, port: int, token: Optional[str] = None) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._token = token
        self._client = httpx.Client(base_url=self.base_url, timeout=20.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ClusterClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def headers(
        self,
        *,
        host: Optional[str] = NON_LOCALHOST_HOST_HEADER,
        token: Optional[str] = None,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        if host is not None:
            result["Host"] = host
        bearer = self._token if token is None else token
        if bearer:
            result["Authorization"] = f"Bearer {bearer}"
        return result

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.post(path, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _require_no_dotenv() -> None:
    if (REPO_ROOT / ".env").exists():
        pytest.skip(
            "a <repo-root>/.env exists, so compose interpolates from it and this "
            "suite's 'a fresh clone, nothing configured' precondition (AC-1) no "
            "longer holds. Move or rename it and re-run."
        )


@pytest.fixture
def cluster(compose_stack: ComposeStack) -> Iterator[ClusterClient]:
    """
    A `ClusterClient` over the published port, carrying a freshly minted JWT.

    Named `cluster`, NOT `client`: `backend/tests/conftest.py` already defines a
    function-scoped `client` fixture returning an `AsyncClient` over
    `ASGITransport`, and shadowing that name would either break the rest of the
    suite or be shadowed by it - both of which happened during development, with
    a coroutine where an HTTP response was expected.

    Minted per test rather than once per session on purpose: MUST M-5.2 deletes
    both volumes, which regenerates the signing key and invalidates every JWT the
    suite held. Re-minting keeps the suite order-independent (D3).
    """
    jwt = _mint_jwt(compose_stack)
    with ClusterClient(compose_stack.port, token=jwt) as instance:
        yield instance


def _mint_jwt(stack: ComposeStack) -> str:
    """`POST /api/v1/auth/token` with the persisted shared token -> a JWT.

    Protects `backend/api/v1/auth.py:57-88` and `docker-compose.yaml:167-173`.
    This is the whole first-run story: with no `.env` and no `SHARED_TOKEN`, the
    cluster generates one, persists it to the `shc-state` volume, names the file
    in a startup WARNING and then answers a real login request.

    **The control is a WRONG token refused first.** Without it, a 200 here could
    be the endpoint ignoring the credential entirely - which is the trap
    `backend/tests/integration/test_signing_key_rotation.py:12-19` documents,
    in a different place.
    """
    with ClusterClient(stack.port) as instance:
        wrong = instance.post(
            "/api/v1/auth/token", json={"shared_token": "not-the-shared-token"}
        )
        assert wrong.status_code in (401, 403), (
            f"a WRONG shared token was answered {wrong.status_code}, not 401/403: "
            f"{wrong.text}. Control assertion for the 200 below - without it, a "
            "200 proves nothing about the credential."
        )
        response = instance.post(
            "/api/v1/auth/token", json={"shared_token": stack.shared_token()}
        )
    assert response.status_code == 200, (
        f"the generated shared token did not authenticate: {response.status_code} "
        f"{response.text}"
    )
    jwt = response.json()["access_token"]
    assert jwt.count(".") + 1 == JWT_SEGMENTS, (
        f"expected a three-segment JWT, got a value with {jwt.count('.') + 1} "
        "segments"
    )
    return jwt


@pytest.fixture(scope="session")
def image_id() -> str:
    """``shc:latest``, built once per session if the daemon does not have it.

    D3: the build runs ``npm ci``, a lockfile platform check and a full vite
    build, so it happens at most once per session and never per-test.
    """
    existing = _run_plain(
        ["docker", "image", "inspect", IMAGE_TAG, "--format", "{{.Id}}"]
    )
    if existing.returncode != 0:
        build = run_compose(compose_argv(None, "build"), check=False)
        if build.returncode != 0:
            raise AssertionError(
                f"{IMAGE_TAG} is not present and `docker compose build` failed:\n"
                f"{build.describe()}\n"
                "The build needs the frontend submodule checked out "
                "(`git submodule update --init --recursive`); see "
                "docker/Dockerfile:12-21."
            )
    return _run_plain(
        ["docker", "image", "inspect", IMAGE_TAG, "--format", "{{.Id}}"]
    ).stdout.strip()


@pytest.fixture(scope="session")
def compose_run() -> ComposeRun:
    """A single-file ``docker compose config --format json``.

    A session fixture because AC-1 asserts on the exit code and on stderr, which
    means the invocation itself is part of what is under test - a test cannot
    re-derive "it exits 0" from a fixture that already asserted it.
    """
    _require_no_dotenv()
    return checked_in_run("config", "--format", "json")


@pytest.fixture
def compose_config(compose_run: ComposeRun) -> dict[str, Any]:
    """The resolved single-file configuration as a dict."""
    return parse_config(compose_run)


@pytest.fixture(scope="session")
def sentinel_image_id() -> str:
    """A second build of the image carrying a sentinel ``VITE_API_URL`` (S-2).

    Closes the gap MUST M-2.4 cannot: with the default build, a literal match on
    ``http://localhost:8000/api/v1`` cannot distinguish "compose passed the
    build arg" from "the developer's local ``frontend/.env.production`` happened
    to hold the same value". ``.dockerignore:72-77`` excludes that file from the
    build context, so this build's bundle can only carry the sentinel.

    Guarded by its own opt-in variable, because it costs a full vite build. The
    measurement is recorded in the issue's report.
    """
    if os.environ.get("SHC_COMPOSE_SENTINEL_BUILD") != "1":
        pytest.skip(
            "SHOULD S-2 (the sentinel rebuild) costs a full `npm ci` + vite "
            "build. Re-run with SHC_COMPOSE_SENTINEL_BUILD=1 to include it; "
            "MUST M-2.4 still catches the relative-URL regression without it."
        )
    sentinel_url = "https://compose-e2e.invalid/api/v1"
    build = run_compose(
        [
            "docker",
            "build",
            "-f",
            str(DOCKERFILE),
            "-t",
            SENTINEL_IMAGE_TAG,
            "--build-arg",
            f"VITE_API_URL={sentinel_url}",
            ".",
        ],
        check=False,
    )
    if build.returncode != 0:
        raise AssertionError(f"the sentinel build failed:\n{build.describe()}")
    return SENTINEL_IMAGE_TAG


def _override_yaml(stack: ComposeStack) -> str:
    """The generated override, as text.

    Written into a temporary directory and never into the repository (D6).
    It renames exactly two things that are unsafe to share on a developer
    machine - the two pinned volume NAMES and the single hardcoded PUBLISHED
    PORT - and changes nothing else.

    ``ports`` uses ``!override``, NOT ``!reset``. MEASURED on Compose v5.6.0:
    ``!reset`` deletes the ``ports:`` key entirely, so the stack would start
    happily, publish nothing, and every live-container scenario would fail with
    a connection refused that reads like an application bug.
    """
    return "\n".join(
        [
            "# GENERATED by backend/tests/compose/conftest.py (issue #134).",
            "# Never written into the repository.",
            f"name: {stack.project_name}",
            "services:",
            "  backend:",
            "    ports: !override",
            f'      - "{stack.port}:8000"',
            "volumes:",
            "  shc-data:",
            f"    name: {stack.data_volume}",
            "  shc-state:",
            f"    name: {stack.state_volume}",
            "",
        ]
    )


@pytest.fixture(scope="session")
def compose_stack(tmp_path_factory, image_id) -> Iterator[ComposeStack]:
    """
    The one long-lived stack, behind a generated override.

    Session-scoped, built once (D3). Brought up before the first scenario and
    taken down at session end, addressed by EXACT name and nothing else
    (D4c.3-5).

    MUST M-3.7 (the first-start token WARNING) depends on those volumes being
    FRESH: ``config.py:309`` persists the token and ``docker/README.md:85-86``
    says the WARNING is first-start only, so a reused volume makes the scenario
    vacuous. That is why the override's volumes are load-bearing, not optional.

    Lifecycle scenarios tear this stack down and put it back themselves (D3) -
    see ``live_stack`` in ``test_lifecycle.py`` - so no test may depend on this
    fixture beyond "a running stack".
    """
    stack = ComposeStack(
        tag=f"shc-e2e-{uuid.uuid4().hex[:12]}",
        port=free_port(),
        override_path=tmp_path_factory.mktemp("shc-compose-e2e") / "override.yaml",
        workdir=tmp_path_factory.mktemp("shc-compose-e2e-work"),
    )

    # (c.2) Refuse to proceed if anything this stack would own already exists.
    # A uuid4 collision is impossible, so a hit means the name was reused, and
    # proceeding would destroy something the suite did not create.
    for name in stack.volume_names:
        if volume_exists(name):
            raise InterlockViolation(
                f"refusing to start: volume {name} already exists. The names are "
                "uuid4-derived, so a hit means the name was reused rather than "
                "freshly minted, and proceeding would delete data this suite did "
                "not create."
            )

    # (c.3) Record, by exact name, everything this fixture is about to create.
    created_volumes = list(stack.volume_names)
    created_temp = Path(stack.workdir)

    stack.override_path.parent.mkdir(parents=True, exist_ok=True)
    stack.override_path.write_text(_override_yaml(stack), encoding="utf-8")

    # D5: preflight and skip loudly rather than fail obscurely. A port taken
    # between selection and `up` otherwise surfaces as a compose bind error that
    # reads like a compose bug.
    holder = port_holder(stack.port)
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        try:
            sock.bind(("127.0.0.1", stack.port))
        except OSError:
            pytest.skip(
                f"the dynamically chosen published port {stack.port} is already "
                f"held by {holder}. Re-run; the suite picks a free port per "
                "session and refuses to fight for one (D5)."
            )

    try:
        stack.preflight_bind_root()
        stack.up()
        # Captured here, not in the test that asserts on it. MUST M-3.7 is only
        # meaningful on a FIRST start: `config.py:309` persists the token and
        # `docker/README.md:85-86` says the WARNING is first-start only. A
        # lifecycle scenario will recreate the container, so the evidence has to
        # be taken before any test runs - which also makes the scenario
        # order-independent, as D3 requires.
        stack.first_start_logs = stack.logs()
        stack.first_start_token = stack.shared_token()
        yield stack
    finally:
        # D4(d): remove ONLY the job directories this session created, by exact
        # name. Never clear the folder wholesale - it is operator data.
        problems = remove_submitted_job_dirs(stack)
        problems += teardown_stack(stack, created_volumes, created_temp)
        if problems:
            raise AssertionError(
                "compose suite teardown:\n  - " + "\n  - ".join(problems)
            )


def remove_submitted_job_dirs(stack: "ComposeStack") -> list[str]:
    """Remove ``<bind-source>/jobs/<id>`` for each id this session submitted.

    By exact name (D4d.4). Anything unrecognised is reported, not removed - the
    same rule the volume teardown follows.
    """
    problems: list[str] = []
    if not stack.submitted_jobs:
        return problems
    jobs_root = stack.bind_source() / "jobs"
    for job_id in sorted(stack.submitted_jobs):
        directory = jobs_root / job_id
        if not directory.exists():
            continue
        shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():  # pragma: no cover - a locked file on Windows
            problems.append(f"could not remove the job directory {directory}")
    return problems


def teardown_stack(
    stack: ComposeStack,
    created_volumes: Sequence[str],
    created_temp: Path,
) -> list[str]:
    """
    Session teardown. Removes ONLY the containers, volumes and directory this
    fixture created, addressed by exact name.

    A stack a lifecycle scenario already tore down with ``down -v`` leaves
    nothing to remove, which is not an error. Anything still present is removed
    through the interlock; if the destructive flag was not set, the leftovers are
    reported BY NAME so a human can delete exactly those and nothing else.
    Returns the list of problems rather than raising, so the caller can fold in
    the job-folder cleanup.
    """
    problems: list[str] = []
    stack.assert_not_checked_in_volumes()

    if destructive_allowed():
        stack.destructive("down", "-v", check=False)
        leftovers = [name for name in created_volumes if volume_exists(name)]
        if leftovers:
            problems.append(
                f"teardown could not remove {leftovers} for project "
                f"{stack.project_name}."
            )
    else:
        present = [name for name in created_volumes if volume_exists(name)]
        if present:
            problems.append(
                f"{FORCE_ENV}=1 but not {DESTRUCTIVE_ENV}=1, so the suite "
                f"cannot remove the throwaway volumes it created: {present}. "
                "Delete them by hand, or re-run with both variables set. They "
                "are NOT shc-data / shc-state."
            )

    if created_temp.exists():
        shutil.rmtree(created_temp, ignore_errors=True)

    return problems + audit_destructive_invocations()


def audit_destructive_invocations() -> list[str]:
    """
    AC-21's second half, audited over the WHOLE session rather than asserted
    inside a single test.

    pytest gives no cross-module ordering guarantee, so one test could run before
    the lifecycle module that issues the ``down -v``. This walks every argv the
    suite actually built and reports any destructive verb that went out without
    the generated override in its ``-f`` flags - which would have resolved to
    the checked-in file's own ``shc-data`` / ``shc-state``.
    """
    offenders: list[str] = []
    for argv in COMPOSE_INVOCATIONS:
        if "down" not in argv and "rm" not in argv:
            continue
        if "-v" not in argv and "--volumes" not in argv:
            continue
        files = [argv[i + 1] for i, token in enumerate(argv) if token == "-f"]
        if len(files) < 2:
            offenders.append(" ".join(argv))
    return (
        [
            "the suite issued a destructive verb without the generated override in "
            f"its -f flags: {offenders}. Such an invocation resolves to the "
            "checked-in file's own shc-data / shc-state volumes."
        ]
        if offenders
        else []
    )
