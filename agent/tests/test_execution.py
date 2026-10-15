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
import re
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, cast

import psutil
import pytest

from agent import executor as executor_module
from agent.executor import (
    DOCUMENTED_STOP_S,
    JOB_ERRORS,
    READER_JOIN_S,
    STOP_GRACE_S,
    STOP_GRACE_SHARE,
    STOP_VERIFY_POLL_S,
    STOP_VERIFY_TIMEOUT_S,
    ExecutionResult,
    execute_job,
    resolve_working_dir,
    stop_budget,
    validate_and_prepare_working_dir,
)
from agent.paths import AgentPaths, resolve_paths
from agent.process import ProcessTree
from agent.tests.conftest import (
    REPO_ROOT,
    Tree,
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

#: The one file allowed to signal the pytest process, and why.
#:
#: ``agent/tests/test_loop.py`` is the only one, and signalling the runner is
#: **issue #124's own subject** -- "the agent suite kills pytest on Windows",
#: measured, exit 15. Flagging it from here would add a sixteenth failure to a
#: fifteen-failure baseline and bury the change that matters, and the fix
#: belongs to #124. The entry is not a standing waiver:
#: :func:`test_the_exemption_does_not_outlive_the_defect_it_exists_for` fails
#: the moment the file stops signalling itself.
SIGNAL_SELF_EXEMPT: frozenset[str] = frozenset({"test_loop.py"})

#: Generous, and deliberately not tight: the tree is waited on by property, so
#: a slow machine costs nothing here while a hard-coded fraction of a second
#: would make this the flakiest test in the suite.
JOB_TIMEOUT_S = 30.0


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


async def wait_for_tree(tree: Tree) -> list[int]:
    """Wait for every rank the tree should have, not for a floor.

    Three on Windows -- ``cmd.exe``, the python parent, the python grandchild
    -- and two on POSIX, where ``/bin/sh -c`` execs its only command so the
    parent *is* the launcher. Waiting for the exact count is what stops the
    Windows arm from passing on two ranks while the third, the one that
    orphans, is never looked at (issue #125 S3).
    """
    await wait_until(
        lambda: len(tree.recorded()) >= tree.expected_pids,
        f"the tree recorded {len(tree.recorded())} of {tree.expected_pids} "
        f"pids in {tree.pids_file}",
    )
    return tree.recorded()


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
    tree_script: Callable[..., Tree],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-9: three processes, zero survivors, on whichever platform this is.

    Never skipped on either platform. A Windows-only stop that is
    ``sys.platform``-ed out on Linux is exercised nowhere, which is how the
    code that only ever worked on POSIX reached the agent in the first place
    (issue #125 Q8c).
    """
    tree = tree_script()
    command = tree.command
    spec = harness.spec(command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        task = asyncio.create_task(run_job(harness, spec, stop_event))
        recorded = await wait_for_tree(tree)
        stop_event.set()
        result = await task

    assert len(recorded) == tree.expected_pids, recorded
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
    tree_script: Callable[..., Tree],
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
    tree = tree_script(refuse_polite=True)
    command = tree.command
    spec = harness.spec(command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        task = asyncio.create_task(run_job(harness, spec, stop_event))
        recorded = await wait_for_tree(tree)
        stop_event.set()
        result = await task

    assert result.error_code == "stopped:shutdown"
    await assert_gone(recorded)

    messages = [record.getMessage() for record in caplog.records]
    assert any("job job-1 required the forced stop" in m for m in messages), messages
    assert any("verified: 0 processes remain" in m for m in messages), messages


async def test_a_grandchild_that_outlives_its_parent_is_not_a_clean_stop(
    harness: Harness,
    tree_script: Callable[..., Tree],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1: a launcher that exits is not a tree that stopped.

    The reproduction, not a paraphrase of it: the parent cooperates and the
    grandchild does not, so ``cmd.exe`` and the parent both exit inside the
    grace. The graceful path used to declare the job finished on exactly that
    basis -- ``verify()`` ran only after the forced tier -- and wrote
    ``stopped:shutdown`` while the grandchild went on holding its pipes, its
    working directory and its GPUs.

    Before the fix this test fails twice over: the escalation is not logged,
    and the grandchild is still alive at the end.
    """
    monkeypatch.setattr(executor_module, "STOP_GRACE_S", 0.5)
    monkeypatch.setattr(executor_module, "STOP_VERIFY_TIMEOUT_S", 0.5)
    tree = tree_script(refuse_polite_child=True)
    spec = harness.spec(tree.command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        task = asyncio.create_task(run_job(harness, spec, stop_event))
        recorded = await wait_for_tree(tree)
        stop_event.set()
        result = await task

    try:
        assert result.error_code == "stopped:shutdown"
        await assert_gone(recorded)

        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "job job-1 stopped its launcher but the tree is still running" in m
            for m in messages
        ), (
            "the launcher exiting must not be read as the tree stopping; that "
            f"is the whole defect. Got: {messages}"
        )
        assert any(
            "job job-1 required the forced stop" in m for m in messages
        ), messages
        assert any("verified: 0 processes remain" in m for m in messages), messages
    finally:
        kill_anything_left_alive(recorded)


async def test_a_degraded_node_says_it_could_not_confirm_the_stop(
    harness: Harness,
    tree_script: Callable[..., Tree],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1 on AC-12's own fallback mode: report, do not assume.

    With no job object the only mechanism that can reach a descendant is
    ``taskkill /T``, which walks parent/child links **from the launcher** --
    and the launcher is exactly what has already exited. So on such a node the
    agent genuinely cannot finish this stop, and the contract is that it says
    so: ``stop_failed:tree_survived`` and an ERROR, never
    ``stopped:shutdown`` on a guess.

    The grandchild genuinely survives here, and the test cleans it up rather
    than pretending otherwise. This is the honest edge of the design and it is
    recorded as a test so it cannot be quietly closed.
    """
    monkeypatch.setattr(executor_module, "STOP_GRACE_S", 0.5)
    monkeypatch.setattr(executor_module, "STOP_VERIFY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(executor_module, "ProcessTree", _degraded_tree)
    tree = tree_script(refuse_polite_child=True)
    spec = harness.spec(tree.command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="agent.executor"):
        task = asyncio.create_task(run_job(harness, spec, stop_event))
        recorded = await wait_for_tree(tree)
        stop_event.set()
        result = await task

    try:
        assert result.error_code == "stop_failed:tree_survived"
        assert harness.read_state().error == "stop_failed:tree_survived"
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "job job-1: the tree still has processes" in m for m in messages
        ), messages
    finally:
        kill_anything_left_alive(recorded)


def kill_anything_left_alive(recorded: list[int]) -> None:
    """End any rank a test deliberately left running.

    ``BoundedLineWriter`` and the stop path both end up waiting on a process
    that has been told to sleep for five minutes, and a suite that leaks one
    per run is a suite that fails on somebody else's machine next week.
    """
    for pid in recorded:
        if not psutil.pid_exists(pid):
            continue
        try:
            process = psutil.Process(pid)
            process.terminate()
            gone, _alive = psutil.wait_procs([process], timeout=5.0)
            for survivor in gone:
                survivor.kill()
        except psutil.Error:
            continue


def _degraded_tree(proc: subprocess.Popen, *, log: Any, create_job: Any = None) -> Any:
    """A :class:`ProcessTree` with the job object taken away, as AC-12 makes it."""
    return ProcessTree(proc, log=log, create_job=lambda _log: None)


def _unreadable_tree(
    proc: subprocess.Popen, *, log: Any, create_job: Any = None
) -> Any:
    """A tree whose process snapshot is broken, as a denied one would be.

    The worst combination: no job to ask, and no way to enumerate what is
    still running. This is the state round-1's S1 fix could be pushed into by
    a failed snapshot, and it reported a clean stop for a live tree.
    """
    return ProcessTree(proc, log=log, create_job=lambda _log: None)


class BrokenSnapshotKernel32:
    """``kernel32`` whose snapshot call returns the documented failure value.

    ``INVALID_HANDLE_VALUE`` with ``restype = wintypes.HANDLE`` arrives as
    **18446744073709551615**, not ``-1``, so a comparison against ``-1`` is
    never true on 64-bit and a broken probe walks on into a call that can only
    fail (issue #125 N1).
    """

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self.first_called = False

    def CreateToolhelp32Snapshot(self, flags: int, pid: int) -> int:  # noqa: N802
        return self.handle

    def Process32FirstW(self, snapshot: int, entry: Any) -> int:  # noqa: N802
        self.first_called = True
        return 0

    def Process32NextW(self, snapshot: int, entry: Any) -> int:  # noqa: N802
        return 0

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        return 1


@pytest.fixture
def broken_snapshot(monkeypatch: pytest.MonkeyPatch) -> BrokenSnapshotKernel32:
    """Make every process snapshot fail, the way a denied one would."""
    import ctypes
    from ctypes import wintypes

    import agent.process as process_module

    invalid = cast(int, ctypes.c_void_p(-1).value)
    kernel32 = BrokenSnapshotKernel32(invalid)
    monkeypatch.setattr(
        process_module,
        "_toolhelp",
        lambda: (ctypes, wintypes, kernel32, None, invalid),
    )
    return kernel32


async def test_a_stop_cannot_be_confirmed_is_never_reported_as_a_clean_one(
    harness: Harness,
    tree_script: Callable[..., Tree],
    monkeypatch: pytest.MonkeyPatch,
    broken_snapshot: BrokenSnapshotKernel32,
) -> None:
    """N1, end to end: a broken probe must produce ``stop_failed``, not ``stopped``.

    QA's reproduction, exactly: the grandchild refuses the polite request and
    the snapshot is broken, so the agent has neither the job's accounting (the
    job object is taken away, as on a degraded node) nor a way to enumerate the
    tree. The old code returned ``stopped:shutdown`` here with the grandchild
    still running, which is the one answer this whole path must never give.

    The grandchild really does survive here -- there is nothing left to kill it
    with -- so the test cleans it up rather than pretending otherwise. What is
    under test is the *report*.
    """
    monkeypatch.setattr(executor_module, "STOP_GRACE_S", 0.5)
    monkeypatch.setattr(executor_module, "STOP_VERIFY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(executor_module, "ProcessTree", _unreadable_tree)
    tree = tree_script(refuse_polite_child=True)
    spec = harness.spec(tree.command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    task = asyncio.create_task(run_job(harness, spec, stop_event))
    recorded = await wait_for_tree(tree)
    stop_event.set()
    result = await task

    try:
        assert (
            result.error_code == "stop_failed:tree_survived"
        ), "an unconfirmable stop is the one thing this must never call clean"
        assert harness.read_state().error == "stop_failed:tree_survived"
        assert not broken_snapshot.first_called, (
            "INVALID_HANDLE_VALUE was not recognised, so the probe walked on "
            "into a call that could only fail and reported an empty host"
        )
    finally:
        kill_anything_left_alive(recorded)


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
    tree_script: Callable[..., Tree],
) -> None:
    """Q9: cancellation is not the stop event, and it must not leak a tree.

    ``asyncio.wait_for`` at the shutdown-sequence call site raises exactly
    this, so a cancelled execution task that leaves a tree behind would be the
    defect this issue exists to remove, reintroduced by the plumbing.
    """
    tree = tree_script()
    command = tree.command
    spec = harness.spec(command)
    harness.claim(spec)

    task = asyncio.create_task(run_job(harness, spec, asyncio.Event()))
    recorded = await wait_for_tree(tree)
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
    tree_script: Callable[..., Tree],
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
    tree = tree_script(refuse_polite=True)
    command = tree.command
    spec = harness.spec(command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    task = asyncio.create_task(run_job(harness, spec, stop_event))
    recorded = await wait_for_tree(tree)
    stop_event.set()
    result = await task

    assert result.error_code == "stop_failed:tree_survived"
    assert harness.read_state().error == "stop_failed:tree_survived"
    assert await survivors(recorded) == [], (
        "the stub lied about the verification, but the real forced tier still "
        "ran, so nothing should be left behind"
    )


async def test_a_log_path_is_only_reported_when_the_stream_wrote_something(
    harness: Harness,
) -> None:
    """S6: ``""`` for a stream that produced no file, never a path to nothing.

    ``BoundedLineWriter`` opens lazily, so a job that writes to one stream has
    no file for the other. Handing back a path to a file that does not exist
    makes the caller ``stat`` it and then decide what a missing log means --
    and a command whose newlines ``cmd.exe`` swallows lands here with
    ``COMPLETED``, ``error: null`` and two paths that 404.
    """
    command = python_command("print('only stdout')")
    spec = harness.spec(command)
    harness.claim(spec)

    result = await run_job(harness, spec)

    assert result.exit_code == 0
    assert result.error_code is None
    assert result.stdout_path, "the stream that wrote must name its file"
    assert Path(result.stdout_path).exists()
    assert result.stderr_path == "", "the silent stream has no file to name"


async def test_a_silent_job_reports_no_log_paths_at_all(harness: Harness) -> None:
    """The degenerate case, so the convention is pinned at both ends."""
    spec = harness.spec(python_command("pass"))
    harness.claim(spec)

    result = await run_job(harness, spec)

    assert result.exit_code == 0
    assert result.stdout_path == ""
    assert result.stderr_path == ""


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


@pytest.mark.parametrize(
    "shutdown_grace_s",
    [0.5, 1.0, 5.0, 10.0, 30.0, 300.0],
)
def test_a_stop_never_claims_more_than_half_the_configured_shutdown_grace(
    shutdown_grace_s: float,
) -> None:
    """F1/N4: AC-15's promise, checked against every grace an operator can set.

    ``AGENT_SHUTDOWN_GRACE_S`` is validated only as "greater than zero", so
    "the stop fits inside the shutdown grace" has to hold for *any* legal
    value, not only the default. Two rounds of this have failed: a grace of
    1.0 bought a 5.03 s shutdown (N4), and then a fixed 2 s + 2 s pair of
    reader-thread joins, outside the budget entirely, put a 4.4 s floor on the
    one path where the tree survives (F1).

    So the count of waits is what matters: a graceful grace, a verification per
    tier, and a join allowance per reader thread. All five are inside, or the
    arithmetic is not a bound.
    """
    grace, verify, join = stop_budget(shutdown_grace_s)
    worst_case = grace + 2 * verify + 2 * join

    assert grace > 0, "a stop that cannot wait is not a stop"
    assert verify > 0
    assert join > 0
    assert worst_case <= STOP_GRACE_SHARE * shutdown_grace_s + 1e-9, (
        f"a stop may claim at most {STOP_GRACE_SHARE:.2f} of {shutdown_grace_s}s, "
        f"and the worst case is grace + 2 verifies + 2 joins = {worst_case:.3f}s"
    )


def test_a_budget_smaller_than_one_poll_is_floored_at_one_poll() -> None:
    """F7: arithmetic that small is worse than no answer at all.

    ``stop_budget(1e-9)`` is arithmetically positive and useless -- a grace or a
    verification shorter than one poll cannot make progress, so every such stop
    degenerates to "forced, and unconfirmed", which is a *worse* report than a
    stop that was allowed to try.

    The floor makes the bound honest rather than unconditional, and the price is
    named rather than hidden: below the documented crossover the stop may take
    more than its share. A 10 ms grace is not a budget an agent can honour;
    pretending otherwise would only hide it.
    """
    for absurd in (1e-9, 1e-6, 0.01, 0.1):
        grace, verify, join = stop_budget(absurd)
        assert grace == verify == join == STOP_VERIFY_POLL_S, (
            f"at a {absurd}s grace nothing can make progress, so every wait "
            f"should be one poll; got {(grace, verify, join)}"
        )


def test_the_floor_only_binds_below_the_documented_crossover() -> None:
    """F7: the floor is a floor, not the answer.

    The crossover is computed from the constants rather than written down, so a
    change to any of them moves it rather than quietly falsifying a literal.
    Above it the derivation owns every wait; below it the floor does, and the
    two tests either side of this one say so.
    """
    crossover = (
        STOP_VERIFY_POLL_S * DOCUMENTED_STOP_S / (STOP_GRACE_SHARE * READER_JOIN_S)
    )
    assert 0.25 < crossover < 0.5, (
        f"the crossover moved to {crossover:.3f}s; #93's lifecycle tests use "
        "0.05s and 0.2s and the shipped default is 30s, so this number is a "
        "statement about where the arithmetic stops being meaningful, and the "
        "bound is here to say when it moves"
    )
    for legal in (crossover * 1.01, 0.5, 1.0, 5.0, 30.0):
        grace, verify, join = stop_budget(legal)
        assert min(grace, verify, join) > STOP_VERIFY_POLL_S, (
            f"the floor is binding at {legal:.3f}s, above the {crossover:.3f}s "
            "crossover, where the derivation should own the answer"
        )
    for below in (crossover * 0.99, 0.2, 0.1, 0.05):
        grace, verify, join = stop_budget(below)
        assert min(grace, verify, join) == STOP_VERIFY_POLL_S, (
            f"the floor should own a {below:.3f}s grace; got "
            f"{(grace, verify, join)}"
        )


def test_the_documented_default_is_exactly_what_the_constants_describe() -> None:
    """N4/F1: deriving the budget must not quietly change the shipped default.

    30 s configured, and the three constants -- ``STOP_GRACE_S``,
    ``STOP_VERIFY_TIMEOUT_S`` and ``READER_JOIN_S`` -- are already the numbers
    that fit inside it (19 s of 30). If a future edit changes one of them, this
    says whether the documented behaviour moved or the derivation did.
    """
    assert stop_budget(30.0) == (
        STOP_GRACE_S,
        STOP_VERIFY_TIMEOUT_S,
        READER_JOIN_S,
    )


def test_no_agent_around_the_job_means_the_documented_constants() -> None:
    """``None`` is the "direct caller" case, and keeps the old behaviour."""
    assert stop_budget(None) == (
        STOP_GRACE_S,
        STOP_VERIFY_TIMEOUT_S,
        READER_JOIN_S,
    )


async def test_the_reader_joins_use_the_derived_allowance_not_a_fixed_one(
    harness: Harness,
    tree_script: Callable[..., Tree],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1, pinned on the argument rather than on a clock.

    The wall-clock form of this needs a *surviving* rank to hold the pipes open,
    which needs the AC-12 degraded configuration and a real ``Agent.run`` --
    ``test_loop_shutdown.py`` does that, because it has to. Here the question is
    narrower and exactly answerable: **what timeout is passed to
    ``Thread.join``?** It used to be a literal ``2.0``, twice, outside the
    budget, which is the whole of F1.

    So the join timeouts are recorded and compared against the budget. No
    timing, no surviving process, no flakiness -- and it cannot pass against the
    old code, which passed ``2.0``.
    """
    seen: list[float | None] = []
    real_join = threading.Thread.join

    def recording_join(self: threading.Thread, timeout: float | None = None) -> None:
        seen.append(timeout)
        real_join(self, timeout=timeout)

    monkeypatch.setattr(threading.Thread, "join", recording_join)
    monkeypatch.setattr(executor_module, "READER_JOIN_S", 2.0)

    tree = tree_script()
    spec = harness.spec(tree.command)
    harness.claim(spec)
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_job(harness, spec, stop_event, shutdown_grace_s=1.0))
    recorded = await wait_for_tree(tree)
    stop_event.set()
    await task

    try:
        assert len(seen) == 2, f"expected one join per reader thread, got {seen}"
        expected = stop_budget(1.0)[2]
        assert seen == [expected, expected], (
            f"the joins were given {seen}, not the derived allowance "
            f"{expected:.4f}s -- a fixed timeout outside the budget is the "
            "defect (issue #125 F1)"
        )
        assert 2.0 not in seen, (
            "the join allowance is the module constant, so the budget is not "
            "being applied to it at all"
        )
    finally:
        kill_anything_left_alive(recorded)


async def test_a_job_stop_fits_the_budget_it_was_given(
    harness: Harness,
    tree_script: Callable[..., Tree],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N4, at the level the executor spends the time: a refusing tree.

    The module constants are monkeypatched *high* precisely so the test can see
    which pair the executor used: with them at 5 s and a configured grace of
    1 s, the stop must take well under a second, not five.
    """
    monkeypatch.setattr(executor_module, "STOP_GRACE_S", 5.0)
    monkeypatch.setattr(executor_module, "STOP_VERIFY_TIMEOUT_S", 5.0)
    tree = tree_script(refuse_polite=True)
    spec = harness.spec(tree.command)
    harness.claim(spec)
    stop_event = asyncio.Event()

    task = asyncio.create_task(run_job(harness, spec, stop_event, shutdown_grace_s=1.0))
    recorded = await wait_for_tree(tree)
    stop_event.set()
    started = time.monotonic()
    result = await task
    elapsed = time.monotonic() - started

    try:
        assert result.error_code == "stopped:shutdown"
        assert elapsed < 1.0, (
            "a stop sized by the module constants overruns a 1.0s configured "
            f"grace by {elapsed:.2f}s (issue #125 N4)"
        )
    finally:
        kill_anything_left_alive(recorded)


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
    """S4/AC-18: the guard has to cover the shapes #124 actually is.

    ``os.kill``'s **second** argument is the signal, not the pid. Everything
    except a literal ``0`` is fatal to this process: at ``SIG_DFL`` the kernel
    kills pytest mid-file with no traceback and no summary on POSIX, and
    ``os.kill(pid, 9)`` is literally ``TerminateProcess(handle, 9)`` on Windows.
    A **variable** signal is the shape that matters, because
    ``agent/tests/test_loop.py`` sends ``os.kill(os.getpid(), sig)`` with
    ``sig`` in hand -- and a guard that only reads integer constants sees
    nothing there. ``os.killpg`` is banned outright: a group-directed signal
    from a test reaches the runner's own group, and the only way to be sure it
    does not is to not send one.
    """
    offenders: list[str] = []
    for path in sorted(AGENT_TESTS.rglob("*.py")):
        if path.name in SIGNAL_SELF_EXEMPT:
            continue
        offenders.extend(f"{path.name}:{line}" for line in _self_signals(path))

    assert offenders == [], (
        "a test must not signal the process running the suite: at SIG_DFL that "
        f"is TerminateProcess on Windows and a silent mid-file death on POSIX "
        f"(issue #124). Offenders: {offenders}"
    )


def _self_signals(path: Path) -> list[int]:
    """Lines in ``path`` that send this process a terminating signal."""
    found: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name) or owner.id != "os":
            continue
        if node.func.attr == "killpg":
            found.append(node.lineno)
        elif node.func.attr == "kill":
            signal_arg = _signal_argument(node)
            if signal_arg is None:
                found.append(node.lineno)
            elif not (
                isinstance(signal_arg, ast.Constant)
                and signal_arg.value == 0
                and isinstance(signal_arg.value, int)
            ):
                found.append(node.lineno)
    return found


def _signal_argument(node: ast.Call) -> ast.expr | None:
    """The signal argument of an ``os.kill`` call, positional or by keyword."""
    if len(node.args) >= 2:
        return node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "sig":
            return keyword.value
    return None


def test_the_exemption_does_not_outlive_the_defect_it_exists_for() -> None:
    """The one exemption is self-cancelling, so it cannot become a loophole.

    ``test_loop.py`` is exempt because signalling the runner *is* issue #124's
    subject and its own defect; flagging it here would add a sixteenth failure
    to a fifteen-failure baseline and bury the change that matters. When #124
    lands, this assertion fails and says so.
    """
    for name in sorted(SIGNAL_SELF_EXEMPT):
        path = AGENT_TESTS / name
        assert path.exists(), f"{name} is exempt from the #124 guard but is gone"
        assert _self_signals(path), (
            f"{name} is exempt but no longer signals the runner; drop it from "
            "SIGNAL_SELF_EXEMPT so the guard covers it again"
        )


def test_two_threads_waiting_on_one_process_agree_on_its_status() -> None:
    """S8: the loser of a ``Popen.wait`` race must not invent a status.

    The executor parks one thread in ``proc.wait()`` and :meth:`ProcessTree.verify`
    asks a second one -- and it asks with a **timeout**, which is the other
    branch of ``Popen._wait``. Both are raced here, because both reach
    ``os.waitpid`` and both are held by the same library invariant. On POSIX the
    losing thread would take ``ChildProcessError`` -- which CPython translates
    to ``sts = 0``, i.e. **a forcibly killed job reported as ``COMPLETED``**.

    It cannot happen, and the reason is worth pinning because it is a library
    invariant rather than a property of this code: since CPython 3.9
    (``subprocess.py``, ``Popen._wait``) every caller holds ``_waitpid_lock`` and
    re-checks ``self.returncode`` under it -- ``break  # Another thread waited.``
    -- so the loser never reaches ``_try_wait`` at all. This test passes on
    Windows by construction (handle-based waiting) and is the guard that proves
    it on a POSIX host, which is where the failure would land.
    """
    untimed = _race_waiters(timeout=None)
    assert sorted(untimed) == [7, 7, 7, 7], (
        "a losing Popen.wait fabricated a status; a forced job would be "
        f"written down as COMPLETED. Got: {untimed}"
    )
    timed = _race_waiters(timeout=30.0)
    assert sorted(timed) == [7, 7, 7, 7], (
        "the timed branch -- the one verify() actually uses -- is not held the "
        f"same way. Got: {timed}"
    )


def _race_waiters(timeout: float | None) -> list[int]:
    """Four threads released together onto one ``Popen``, and what each saw."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    statuses: list[int] = []
    start = threading.Barrier(5)

    def wait() -> None:
        start.wait(timeout=30)
        statuses.append(proc.wait(timeout=timeout))

    waiters = [threading.Thread(target=wait) for _ in range(4)]
    for thread in waiters:
        thread.start()
    start.wait(timeout=30)
    for thread in waiters:
        thread.join(timeout=30)
    return statuses


# --- AC-19: the documentation the record requires --------------------------


def _spec_text() -> str:
    return (REPO_ROOT / "docs" / "specs" / "125-windows-job-execution.md").read_text(
        encoding="utf-8"
    )


def _corrections_index(text: str) -> dict[str, str]:
    """``{id: what it now says}`` from §12.9's table, and only from there."""
    table = text.split("### 12.9 Corrections index", 1)[-1]
    found: dict[str, str] = {}
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"C\d+", cells[0]):
            found[cells[0]] = cells[-1]
    return found


def test_the_header_cannot_disagree_with_the_corrections_index() -> None:
    """F3: the record must not contradict itself about its own retractions.

    This header is the first thing a reader sees, and it used to carry two
    things §12 had already fixed: an amendments table stopping at C6 while the
    index held C10, and the permanent-breakaway claim that §12.1 had withdrawn
    by name. Both were hand-maintained, which is the actual defect — a summary
    of a summary is wrong the moment either moves.

    So the header no longer restates the index. It points at it, states a
    count, and this test reads §12.9 and fails if the count drifts. Markdown
    cannot literally derive one block from another; a test that compares them
    is the nearest honest equivalent, and it is closer than a hand-kept table
    because it cannot rot silently.
    """
    text = _spec_text()
    index = _corrections_index(text)
    header = text.split("## 0. Baseline", 1)[0]

    assert len(index) >= 12, (
        f"§12.9 indexes only {sorted(index)}; the index is the single source the "
        "header points at, so it is the block that must not be abbreviated"
    )
    assert f"{len(index)} corrections" in header, (
        f"the header must state the index's size ({len(index)} corrections) so it "
        "cannot drift; it does not"
    )
    # No hand-kept amendments table: the header may refer to the index's ids in
    # prose, never restate them as its own rows.
    assert "| C1 |" not in header, (
        "the header keeps its own amendments table again; it is a hand-maintained "
        "copy of §12.9 and has already been wrong twice"
    )
    # A range the index's own bounds must match, so "C1 through C6" fails the
    # moment §12.9 grows. Spelling out all twelve ids in the header would be the
    # hand-maintained copy this test exists to prevent.
    numbers = sorted(int(key[1:]) for key in index)
    span = re.search(r"C(\d+)\s*(?:through|to|–|-)\s*C(\d+)", header)
    assert span is not None, (
        "the header must name the index's bounds as a range (C1 through C<N>), so "
        "it is derived rather than restated"
    )
    assert (int(span.group(1)), int(span.group(2))) == (numbers[0], numbers[-1]), (
        f"the header says C{span.group(1)} through C{span.group(2)}, and §12.9 "
        f"indexes C{numbers[0]} through C{numbers[-1]}"
    )
    # And the withdrawn claim must not be *asserted* up there. Mentioning it in
    # order to retract it is the point, so the rule is per-paragraph: a block
    # carrying a retracted phrase has to carry a withdrawal in the same block.
    # (Per-line would be wrong — the claim is quoted across three lines.)
    retraction_markers = ("withdrawn", "withdrew", "does not reproduce", "retracted")
    retracted = (
        "fails on the very host",
        "CREATE_BREAKAWAY_FROM_JOB",
        "one run in ten",
    )
    paragraphs = re.split(r"\n\s*\n", header)
    offenders = [
        block.strip().replace("\n", " ")
        for block in paragraphs
        if any(phrase in block for phrase in retracted)
        and not any(marker in block for marker in retraction_markers)
    ]
    assert offenders == [], (
        "the header states a claim §12.1 withdrew without retracting it in the "
        "same paragraph, so a reader who stops at the top of the file gets the "
        f"withdrawn version: {offenders} (issue #125 F3)"
    )
    assert any(any(phrase in block for phrase in retracted) for block in paragraphs), (
        "the header no longer mentions the withdrawn claim at all; a retraction "
        "a reader cannot find from the top of the file is not a retraction"
    )


def test_the_spec_attributes_the_probe_cost_to_something_real() -> None:
    """F2: a number in the spec of record must name a cause that reproduces.

    §12.6 attributed ~560 ms of verification to `WinDLL` re-resolution. The
    560 ms reproduces — QA measured 563 ms after the cache was in place, which
    is how the attribution was falsified — but the DLL load is ~8% of a probe.
    The cost is the kernel snapshot and the walk over the host's processes.

    Pinned by requiring the attribution to be stated next to a measurement of
    it. If someone re-derives a cost claim later, this is the test that makes
    them put the split next to it.
    """
    section = _spec_text().split("### 12.6 ", 1)[-1].split("### 12.7 ", 1)[0]

    assert (
        "CreateToolhelp32Snapshot" in section
    ), "§12.6 must name what actually costs: the kernel snapshot and the walk"
    assert "per probe" in section, "§12.6 must quote a per-probe cost, not a total"
    assert (
        "2.296" in section or "2.8" in section
    ), "§12.6 must carry a measured per-probe figure"
    assert "8%" in section, (
        "§12.6 must say how much the cache is actually worth, which is the part "
        "the wrong attribution overstated"
    )


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
