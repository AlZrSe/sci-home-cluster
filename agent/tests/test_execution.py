"""
Job execution, end to end, against the real shell and real processes.

``agent/tests/test_executor.py`` has thirteen tests and none of them launches
anything: it covers ``validate_and_prepare_working_dir``, ``build_child_env``
and ``ExecutionResult``, and nothing else. Issue #98's AC-3, AC-4 and AC-12
were never implemented, which is how a stop path nothing could reach survived
two reviews (issue #125 §1.10).

So everything here asserts on **files and pids**, never on a mock and never on
"no exception was raised". ``logging`` swallows a formatting error inside a
handler, so a test that only checked that nothing blew up would have passed
against the broken affinity line; and ``proc.poll() is not None`` would pass
for an implementation that killed the launcher and orphaned its ranks, which is
the exact failure issue #99's problem statement names.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psutil
import pytest

from agent import executor as executor_module
from agent.executor import (
    JOB_ERRORS,
    ExecutionResult,
    execute_job,
    resolve_working_dir,
    validate_and_prepare_working_dir,
)
from agent.paths import AgentPaths, resolve_paths
from agent.process import ProcessTree
from agent.tests.conftest import (
    REPO_ROOT,
    read_recorded_pids,
    wait_until,
)

from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec
from shared.schemas.resources import Resources

pytestmark = pytest.mark.integration

EXECUTOR_SOURCE = REPO_ROOT / "agent" / "executor.py"
AGENT_TESTS = REPO_ROOT / "agent" / "tests"

#: Generous, and deliberately not tight: the tree is waited on by property, so
#: a slow machine costs nothing here while a hard-coded fraction of a second
#: would make this the flakiest test in the suite.
JOB_TIMEOUT_S = 30.0

#: Enough pids for the launcher → parent → grandchild tree. On Windows the
#: launcher is ``cmd.exe`` and records its own pid, so three; on POSIX
#: ``/bin/sh -c`` execs its only command, so the python parent *is* the
#: launcher and only two are recorded. The assertion is ``>= 2`` so a POSIX
#: shell that does not exec still passes -- the point of the test is that
#: every recorded process is gone, not how many there were.
MIN_TREE_PIDS = 2


@dataclass
class Harness:
    """One node, one job, and the files they share."""

    root: Path
    paths: AgentPaths
    node_spec: NodeSpec

    @property
    def job_id(self) -> str:
        return "job-1"

    def state_file(self) -> Path:
        return self.paths.jobs_dir / self.job_id / "state.yaml"

    def read_state(self) -> JobState:
        return read_yaml(str(self.state_file()), JobState)

    def spec(self, command: str, **kwargs: Any) -> JobSpec:
        return JobSpec(
            name="probe",
            command=command,
            working_dir=str(kwargs.pop("working_dir", self.root)),
            resources=kwargs.pop("resources", Resources(gpus=0, cpus=1, memory_gb=1)),
            **kwargs,
        )

    def claim(self, spec: JobSpec) -> JobState:
        state = JobState(
            job_id=self.job_id,
            spec=spec,
            status=JobStatus.RUNNING,
            node_id="node-01",
            created_at=datetime(2026, 1, 1),
            started_at=datetime(2026, 1, 1),
            retry_count=0,
        )
        write_yaml(str(self.state_file()), state)
        return state


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    root = tmp_path / "syncthing"
    (root / "jobs" / "job-1").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    # is_within_syncthing_root reads os.environ directly and has no injection
    # point; the suite's restore_process_environment fixture undoes this.
    monkeypatch.setenv("SYNCTHING_ROOT", str(root))
    return Harness(
        root=root,
        paths=resolve_paths(root, "node-01"),
        node_spec=NodeSpec(
            node_id="node-01",
            hostname="test-host",
            gpus=[],
            cpus=2,
            memory_gb=4,
            os=sys.platform,
            status="ONLINE",
            last_heartbeat=datetime(2026, 1, 1),
        ),
    )


async def run_job(
    harness: Harness,
    spec: JobSpec,
    stop_event: asyncio.Event | None = None,
    **kwargs: Any,
) -> ExecutionResult:
    """Drive the real ``execute_job`` and return what it wrote."""
    return await asyncio.wait_for(
        execute_job(
            job_id=harness.job_id,
            job_spec=spec,
            node_spec=harness.node_spec,
            paths=harness.paths,
            syncthing_root=harness.root,
            stop_event=stop_event if stop_event is not None else asyncio.Event(),
            **kwargs,
        ),
        timeout=JOB_TIMEOUT_S,
    )


def python_command(program: str) -> str:
    """``"<sys.executable>" -c "<program>"``, quoted end to end.

    One program on both platforms, and quoted because a Windows install under
    ``C:\\Program Files\\Python312\\`` contains a space. ``echo hello`` would
    have been a ``cmd`` built-in here and an ``sh`` built-in on POSIX, with
    different escape processing -- a test that only passes under one of them is
    not testing the thing the agent does (issue #125 Q8a).
    """
    return f'"{sys.executable}" -c "{program}"'


# --- AC-4: a job launches, logs, and reports a terminal state --------------


async def test_a_successful_job_is_completed_and_both_streams_are_captured(
    harness: Harness,
) -> None:
    """AC-4, asserted on the files: two log files and a round-tripped state."""
    command = python_command(
        "import sys; print('OUT-line'); print('ERR-line', file=sys.stderr)"
    )
    harness.claim(harness.spec(command))

    result = await run_job(harness, harness.spec(command))

    assert result.exit_code == 0
    assert result.error_code is None
    assert Path(result.stdout_path).read_text(encoding="utf-8").strip() == "OUT-line"
    assert Path(result.stderr_path).read_text(encoding="utf-8").strip() == "ERR-line"

    state = harness.read_state()
    assert state.status == JobStatus.COMPLETED
    assert state.exit_code == 0
    assert state.error is None
    assert state.completed_at is not None


async def test_a_failing_job_reports_the_real_exit_code_through_state_yaml(
    harness: Harness,
) -> None:
    """AC-4's negative half, and the round trip through ``read_yaml``."""
    command = python_command("import sys; sys.exit(3)")
    harness.claim(harness.spec(command))

    result = await run_job(harness, harness.spec(command))

    assert result.exit_code == 3
    assert result.error_code == "process_failed:exit_code=3"

    state = read_yaml(str(harness.state_file()), JobState)
    assert state.status == JobStatus.FAILED
    assert state.exit_code == 3
    assert state.error == "process_failed:exit_code=3"
    assert state.node_id == "node-01", "the agent never reassigns node_id (#100)"
    assert state.retry_count == 0, "retry_count belongs to the backend (#105)"


async def test_the_launch_line_names_the_live_tree_control_mechanism(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """A degraded node has to be visible without turning on DEBUG."""
    command = python_command("pass")
    harness.claim(harness.spec(command))

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        await run_job(harness, harness.spec(command))

    launches = [
        record.getMessage()
        for record in caplog.records
        if "tree_control=" in record.getMessage()
    ]
    assert len(launches) == 1, f"exactly one launch line: {launches}"
    assert any(
        mode in launches[0]
        for mode in ("process group", "job object", "taskkill fallback", "leader only")
    ), launches[0]


async def test_a_shell_the_agent_cannot_find_is_reported_before_any_launch(
    harness: Harness,
) -> None:
    """Q5: the agent's fault and the submitter's must not share a number."""
    harness.claim(harness.spec(python_command("pass"), working_dir=str(harness.root)))

    result = await run_job(
        harness,
        harness.spec(python_command("pass"), working_dir=str(harness.root)),
        shell="sh-not-installed-anywhere",
    )

    assert result.exit_code == 127
    assert result.error_code == "launch_failed:shell_not_found"
    state = harness.read_state()
    assert state.status == JobStatus.FAILED
    assert state.exit_code == 127
    assert state.error == "launch_failed:shell_not_found"


# --- AC-5: working_dir is decided on the raw string ------------------------


def test_the_default_working_dir_lands_where_this_host_says_it_should(
    harness: Harness,
) -> None:
    """Q3's table, one row per platform, asserted from the schema default.

    The literal ``/sync/projects/new-run`` is absolute on POSIX and **not**
    absolute on Windows, where it becomes ``<root>/sync/projects/new-run`` and
    is created. That is the whole point of branching on ``Path.is_absolute()``
    rather than on a string test for a leading slash.
    """
    default = JobSpec(
        name="probe",
        command="echo hi",
        resources=Resources(gpus=0, cpus=1, memory_gb=1),
    ).working_dir

    resolved = resolve_working_dir(default, harness.root)

    if sys.platform == "win32":
        assert resolved == harness.root / "sync" / "projects" / "new-run"
        _, error = validate_and_prepare_working_dir(default, harness.root)
        assert error is None
        assert resolved.is_dir()
    else:
        assert resolved == Path("/sync/projects/new-run")
        _, error = validate_and_prepare_working_dir(default, harness.root)
        assert error == "launch_failed:working_dir_not_found"


async def test_a_relative_working_dir_is_resolved_against_the_root_not_the_cwd(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-b, the CWD dependence, asserted by moving the CWD somewhere else.

    ``normalize_path`` resolves a relative path against the process's working
    directory and the result then reports ``is_absolute() == True``, so the
    old behaviour produced a *different, real* directory per start location
    rather than an error. The CWD here deliberately contains nothing like the
    project, so the old resolution would have failed the job outright.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    harness.claim(harness.spec(python_command("pass")))

    result = await run_job(
        harness,
        JobSpec(
            name="probe",
            command=python_command("pass"),
            working_dir="projects/x",
            resources=Resources(gpus=0, cpus=1, memory_gb=1),
        ),
    )

    assert result.exit_code == 0
    assert (harness.root / "projects" / "x").is_dir()
    assert not (elsewhere / "projects" / "x").exists()


async def test_an_absolute_working_dir_outside_the_root_must_exist(
    harness: Harness, tmp_path: Path
) -> None:
    """The unchanged half of the contract: outside the root, never created."""
    missing = tmp_path / "outside" / "nope"
    spec = JobSpec(
        name="probe",
        command=python_command("pass"),
        working_dir=str(missing),
        resources=Resources(gpus=0, cpus=1, memory_gb=1),
    )
    harness.claim(spec)

    result = await run_job(harness, spec)

    assert result.error_code == "launch_failed:working_dir_not_found"
    assert not missing.exists(), "a directory outside the root is never created"


# --- AC-9/AC-10: the tree, both platforms, never skipped -------------------


async def wait_for_tree(pids_file: Path) -> list[int]:
    await wait_until(
        lambda: len(read_recorded_pids(pids_file)) >= MIN_TREE_PIDS,
        f"the tree recorded fewer than {MIN_TREE_PIDS} pids in {pids_file}",
    )
    return read_recorded_pids(pids_file)


async def assert_gone(recorded: list[int]) -> None:
    """Every recorded pid must be gone -- the assertion #98 AC-4 asked for.

    ``psutil.pid_exists`` is the one probe that works on both platforms:
    ``os.kill(pid, 0)`` works but its sibling ``os.kill(pid, 9)`` is
    ``TerminateProcess(handle, 9)`` on Windows, and ``OpenProcess`` hands back
    a handle for a pid that has already been reaped (issue #125 C4).
    """
    for pid in recorded:

        def gone(watched: int = pid) -> bool:
            return not psutil.pid_exists(watched)

        await wait_until(gone, f"pid {pid} survived the stop")
        assert psutil.pid_exists(pid) is False


async def survivors(recorded: list[int]) -> list[int]:
    """Which recorded pids are still alive. Polls, then answers."""
    for pid in recorded:

        def gone(watched: int = pid) -> bool:
            return not psutil.pid_exists(watched)

        await wait_until(gone, f"pid {pid} survived the stop")
    return [pid for pid in recorded if psutil.pid_exists(pid)]


async def test_stopping_a_job_leaves_no_process_in_its_tree(
    harness: Harness,
    tree_script: Callable[..., tuple[str, Path]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-9: three processes, zero survivors, on whichever platform this is.

    Never skipped on either platform. A Windows-only stop that is
    ``sys.platform``-ed out on Linux is exercised nowhere, which is how the
    code that only ever worked on POSIX reached the agent in the first place
    (issue #125 Q8c).
    """
    command, pids = tree_script()
    spec = harness.spec(command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        task = asyncio.create_task(run_job(harness, spec, stop_event))
        recorded = await wait_for_tree(pids)
        stop_event.set()
        result = await task

    assert len(recorded) >= MIN_TREE_PIDS, recorded
    assert result.error_code == "stopped:shutdown"
    assert result.exit_code is not None
    await assert_gone(recorded)

    messages = [record.getMessage() for record in caplog.records]
    assert any("job job-1 stopped after the graceful request" in m for m in messages), (
        "a cooperative tree is the graceful tier; if it needed forcing, the "
        f"log says so. Got: {messages}"
    )


async def test_a_tree_that_refuses_the_polite_request_is_forced_and_says_so(
    harness: Harness,
    tree_script: Callable[..., tuple[str, Path]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-10: "pid gone" alone cannot tell a graceful stop from a forced one.

    The child installs ``SIG_IGN`` for ``SIGBREAK`` on Windows and for
    ``SIGTERM`` on POSIX -- the cooperative tier is defeatable on both -- and
    the assertion is on the **log record**, not on the absence of survivors.
    An operator needs to know a job was taken out rather than shut down.

    ``STOP_GRACE_S`` is shortened so the test does not spend the production
    five seconds proving that it waits five seconds; the constant itself is
    unchanged and is asserted separately in ``test_process.py``.
    """
    monkeypatch.setattr(executor_module, "STOP_GRACE_S", 0.5)
    command, pids = tree_script(refuse_polite=True)
    spec = harness.spec(command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        task = asyncio.create_task(run_job(harness, spec, stop_event))
        recorded = await wait_for_tree(pids)
        stop_event.set()
        result = await task

    assert result.error_code == "stopped:shutdown"
    await assert_gone(recorded)

    messages = [record.getMessage() for record in caplog.records]
    assert any("job job-1 required the forced stop" in m for m in messages), messages
    assert any("verified: 0 processes remain" in m for m in messages), messages


async def test_a_stop_before_launch_writes_a_terminal_state_with_no_exit_code(
    harness: Harness,
) -> None:
    """Q10: the stop that arrives before there is a process to report."""
    spec = harness.spec(python_command("print('this must never run')"))
    harness.claim(spec)
    stop_event = asyncio.Event()
    stop_event.set()

    result = await run_job(harness, spec, stop_event)

    assert result.exit_code is None
    assert result.error_code == "stopped:shutdown"
    state = harness.read_state()
    assert state.status == JobStatus.FAILED
    assert state.exit_code is None
    assert state.error == "stopped:shutdown"
    assert state.completed_at is not None


async def test_a_cancelled_execution_task_forces_the_tree_and_re_raises(
    harness: Harness,
    tree_script: Callable[..., tuple[str, Path]],
) -> None:
    """Q9: cancellation is not the stop event, and it must not leak a tree.

    ``asyncio.wait_for`` at the shutdown-sequence call site raises exactly
    this, so a cancelled execution task that leaves a tree behind would be the
    defect this issue exists to remove, reintroduced by the plumbing.
    """
    command, pids = tree_script()
    spec = harness.spec(command)
    harness.claim(spec)

    task = asyncio.create_task(run_job(harness, spec, asyncio.Event()))
    recorded = await wait_for_tree(pids)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_gone(recorded)


# --- AC-11: verification is a read, and its failure is a distinct answer ---


class NeverVerifyingTree(ProcessTree):
    """A real tree whose :meth:`verify` always answers "not yet".

    Delegates every stop to the real implementation so the process really
    dies -- the property under test is what the executor *writes* when the
    verification times out, not whether the kill worked.
    """

    def verify(self, timeout_s: float) -> bool:  # noqa: ARG002 - the point is to ignore it
        return False


async def test_a_timed_out_verification_is_not_reported_as_a_clean_stop(
    harness: Harness,
    tree_script: Callable[..., tuple[str, Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-11's negative case: ``stop_failed:tree_survived``, not ``stopped``.

    The tree has to **refuse the polite request** to get this far: a
    cooperative tree exits inside the grace and the verification is never
    consulted, which is correct and would leave the timeout branch untested.
    Without this test "the tree is gone" is a comment about a function call.
    """
    monkeypatch.setattr(executor_module, "ProcessTree", NeverVerifyingTree)
    monkeypatch.setattr(executor_module, "STOP_GRACE_S", 0.5)
    command, pids = tree_script(refuse_polite=True)
    spec = harness.spec(command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    task = asyncio.create_task(run_job(harness, spec, stop_event))
    recorded = await wait_for_tree(pids)
    stop_event.set()
    result = await task

    assert result.error_code == "stop_failed:tree_survived"
    assert harness.read_state().error == "stop_failed:tree_survived"
    assert await survivors(recorded) == [], (
        "the stub lied about the verification, but the real forced tier still "
        "ran, so nothing should be left behind"
    )


# --- AC-14: no per-node secret reaches the child, end to end ---------------

SECRETS = (
    "SHARED_TOKEN",
    "SECRET_KEY",
    "SHC_STATE_DIR",
    "SYNCTHING_ROOT",
    "NODE_ID",
    "CUSTOM_TOKEN",
    "API_KEY",
)


async def test_no_per_node_secret_reaches_the_child_process(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-14, through ``execute_job`` rather than through ``build_child_env``.

    The unit test above it builds the child environment by hand; a harness
    that does that will happily show the secrets, and did during the PM's
    analysis. This one goes through the only production path there is and
    reads the **stdout log file** the operator would read.
    """
    for name in SECRETS:
        monkeypatch.setenv(name, f"value-of-{name}")
    monkeypatch.setenv("KEEP_THIS", "value")
    command = python_command("import os; print(os.environ)")
    spec = harness.spec(command)
    harness.claim(spec)

    result = await run_job(harness, spec)

    assert result.exit_code == 0
    captured = Path(result.stdout_path).read_text(encoding="utf-8")
    for name in SECRETS:
        assert name not in captured, f"{name} reached the child process"
        assert f"value-of-{name}" not in captured
    assert "KEEP_THIS" in captured


# --- AC-16: resources.cpus is enforced or it says it is advisory -----------


async def test_cpus_is_reported_as_advisory_where_it_cannot_be_enforced(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-16's everywhere-else arm, and the DEBUG line it replaces.

    ``getattr(os, "sched_setaffinity", None)`` is the whole mechanism: there
    is no ``AttributeError`` arm left to hide the answer in.
    """
    if hasattr(os, "sched_setaffinity"):
        pytest.skip("sched_setaffinity is Linux-only; this arm is the other one")
    command = python_command("pass")
    spec = harness.spec(command, resources=Resources(gpus=0, cpus=2, memory_gb=1))
    harness.claim(spec)

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        result = await run_job(harness, spec)

    assert result.exit_code == 0
    rendered = [record.getMessage() for record in caplog.records]
    assert any(
        "resources.cpus=2 is advisory and not enforceable on this platform" in line
        and f"all {os.cpu_count() or 0} CPUs" in line
        for line in rendered
    ), rendered
    assert not any(
        record.levelname == "DEBUG" for record in caplog.records
    ), "the DEBUG-only answer is the defect issue #125 Q6 removes"


@pytest.mark.skipif(
    not hasattr(os, "sched_setaffinity"),
    reason="sched_setaffinity is Linux-only",
)
async def test_cpu_pinning_renders_the_pid_and_the_cpu_set(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-16's Linux arm, asserted on the **rendered** message.

    Asserting "no exception was raised" would have passed against the shipped
    code: the record was ``"set CPU affinity for job %s pid %%d: %%s"`` with
    three arguments, ``logging`` caught the ``TypeError`` in
    ``Handler.handleError``, dropped the line and wrote a traceback to stderr
    for every job with ``cpus > 0`` (issue #125 F-a).
    """
    command = python_command("pass")
    spec = harness.spec(command, resources=Resources(gpus=0, cpus=2, memory_gb=1))
    harness.claim(spec)

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        result = await run_job(harness, spec)

    assert result.exit_code == 0
    rendered = [record.getMessage() for record in caplog.records]
    pinned = [line for line in rendered if "pinned job job-1 to CPUs" in line]
    assert pinned, f"no pinning line at INFO: {rendered}"
    assert "pid " in pinned[0], pinned[0]
    assert ("CPUs {0, 1}" in pinned[0]) or ("CPUs {1, 0}" in pinned[0]), pinned[0]


async def test_asking_for_more_cpus_than_the_node_has_is_a_warning_and_a_launch(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-16's third case, on every platform: the submission is the problem."""
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    command = python_command("pass")
    spec = harness.spec(command, resources=Resources(gpus=0, cpus=8, memory_gb=1))
    harness.claim(spec)

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        result = await run_job(harness, spec)

    assert result.exit_code == 0, "an over-request must not fail the job"
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING"
    ]
    assert any(
        "resources.cpus=8 exceeds this node's 1 CPUs" in line for line in warnings
    ), warnings


# --- the vocabulary and the ladder cannot drift ----------------------------

ERROR_PREFIXES = ("launch_failed:", "process_failed:", "stopped:", "stop_failed:")


def test_every_error_code_the_executor_can_write_is_in_the_vocabulary() -> None:
    """§8's drift guard: a code cannot be invented in a branch and forgotten.

    Collected from the AST rather than by reading the ladder, so a new
    ``f"launch_failed:..."`` anywhere in the module fails this test instead of
    reaching ``state.yaml`` and the dashboard undocumented.
    """
    tree = ast.parse(EXECUTOR_SOURCE.read_text(encoding="utf-8"))
    known = {value.split("{")[0] for value in JOB_ERRORS.values()}
    known |= set(JOB_ERRORS.values())

    written = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(ERROR_PREFIXES)
    }

    assert written <= known, f"undocumented error codes: {sorted(written - known)}"


def test_the_vocabulary_and_the_exit_status_table_are_two_different_tables() -> None:
    """A job's error vocabulary is not an agent exit status.

    ``agent/config.py``'s table exists so issue #104's systemd unit inherits
    one set of codes rather than inventing a second; merging the two is how a
    second table gets invented.
    """
    from agent.config import (
        EXIT_CONFIG_ERROR,
        EXIT_INTERNAL_ERROR,
        EXIT_OK,
        EXIT_SHUTDOWN_GRACE_EXPIRED,
        EXIT_WATCHER_DEAD,
    )

    statuses = {
        EXIT_OK,
        EXIT_CONFIG_ERROR,
        EXIT_INTERNAL_ERROR,
        EXIT_WATCHER_DEAD,
        EXIT_SHUTDOWN_GRACE_EXPIRED,
    }
    assert statuses == {0, 1, 2, 3, 4}
    assert not statuses & set(JOB_ERRORS.values())


# --- AC-17/AC-18: the mechanical pins -------------------------------------


def test_the_executor_carries_no_posix_process_api() -> None:
    """AC-17 and §9: every one of those names has moved to ``agent/process.py``.

    Asserted over the file's *text*, which is the cheapest possible pin and
    the one that catches a literal reintroduction in a hurry. The module's own
    prose is written so this can be a substring search -- a comment that named
    one of them would make the pin ambiguous.
    """
    text = EXECUTOR_SOURCE.read_text(encoding="utf-8")

    for gone in ("killpg", "SIGKILL", "/bin/sh", "start_new_session", "%%d", "%%s"):
        assert gone not in text, f"{gone!r} is back in agent/executor.py"
    assert "import signal" not in text


def test_the_cpu_affinity_call_is_only_ever_probed() -> None:
    """§4 Q6 mandates ``getattr``; a bare attribute reference is the bug.

    A substring search cannot make this distinction -- §9's shorthand grep
    lists the syscall's name, which ``getattr`` necessarily contains -- so
    this walks the AST: the name may appear only as the second argument of a
    ``getattr``, never as ``os.<name>``.
    """
    tree = ast.parse(EXECUTOR_SOURCE.read_text(encoding="utf-8"))
    offenders = [
        f"line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "sched" + "_setaffinity"
    ]

    assert offenders == [], (
        "resources.cpus must be probed with getattr, never referenced "
        f"directly: {offenders}"
    )


def test_no_agent_test_can_kill_the_runner() -> None:
    """AC-18: ``os.kill``'s second argument is the **signal**, not the pid.

    ``os.kill(pid, 9)`` is ``TerminateProcess(handle, 9)`` on Windows, which is
    how issue #124's test takes the pytest process down mid-file with no
    traceback and no summary. ``os.kill(pid, 0)`` is fine -- it is the POSIX
    liveness probe and terminates nothing -- so the ban is on the signal
    argument being a non-zero constant.
    """
    offenders: list[str] = []
    for path in sorted(AGENT_TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "kill"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and len(node.args) == 2
            ):
                continue
            signal = node.args[1]
            if (
                isinstance(signal, ast.Constant)
                and isinstance(signal.value, int)
                and signal.value != 0
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == [], (
        "a non-zero constant signal terminates the pytest process on Windows "
        f"(issue #124): {offenders}"
    )


# --- AC-19: the documentation the record requires --------------------------


def test_the_superseded_specs_point_at_this_one() -> None:
    """A reader who lands on #98 must be told it is no longer the code."""
    superseded = REPO_ROOT / "docs" / "specs" / "98-job-execution.md"
    text = superseded.read_text(encoding="utf-8")

    assert "125-windows-job-execution.md" in text
    assert "superseded" in text.lower()


def test_docs_spec_md_documents_the_execution_model() -> None:
    """Issue #98's AC-2, discharged: the execution model is now documented."""
    text = (REPO_ROOT / "docs" / "spec.md").read_text(encoding="utf-8")

    assert "Job execution: shell and process tree" in text
    assert "cmd.exe" in text
