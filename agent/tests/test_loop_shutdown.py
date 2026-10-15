"""
Shutdown with a job in flight.

**This file is not ``agent/tests/test_loop.py`` and must never be merged into
it.** That file kills the pytest process on Windows (issue #124: ``os.kill`` on
a pid at ``SIG_DFL`` is ``TerminateProcess`` there), so everything issue #125's
AC-13 needs is here, in a file that can be run and reasoned about on its own::

    pytest agent/tests/test_loop_shutdown.py

None of these tests delivers a real signal. The stop is delivered by setting
the agent's own injected ``asyncio.Event``, which is the path the supervisor
and ``run`` consume anyway -- so the assertions are about the shutdown
*sequence* rather than about this platform's signal plumbing, and they run
identically on Windows and on POSIX.

AC-13's remaining half -- a delivered ``SIGTERM`` through ``Agent.run`` -- is
#124's to unblock, and is recorded in ``docs/specs/125-windows-job-execution.md``
§7.1 rather than pretended around here.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

import psutil
import pytest

from agent.config import build_settings
from agent.loop import Agent
from agent.paths import resolve_paths
from agent.tests.conftest import (
    RecordingWatcher,
    Tree,
    wait_until,
)

from shared.schemas.job_status import JobStatus

pytestmark = pytest.mark.unit

#: The #93 test idiom (a fraction of a second rather than 30). AC-13 needs it
#: for the same reason the rest of that file does: a shutdown grace is a budget
#: a test cannot afford to spend four times over.
SHUTDOWN_GRACE_S = 0.5

#: The tree is waited on by property, so this is a *ceiling* for a machine
#: under load, not a sleep.
TREE_TIMEOUT_S = 30.0


def settings_for(root: Path, node_id: str = "node-01", **env: str) -> Any:
    return build_settings(
        ["--node-id", node_id],
        environ={
            "SYNCTHING_ROOT": str(root),
            "AGENT_SHUTDOWN_GRACE_S": str(SHUTDOWN_GRACE_S),
            "AGENT_FOLDER_WATCH_INTERVAL_S": "0.05",
            "AGENT_WATCHER_LIVENESS_INTERVAL_S": "0.05",
            "AGENT_FOLDER_RETRY_MAX_S": "0.3",
            **env,
        },
    )


async def drive_a_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tree_script: Callable[..., Tree],
    *,
    shutdown_grace_s: float,
    refuse_polite: bool = False,
) -> tuple[int, float, list[int], str]:
    """Claim a job, run the agent, stop it, and report what the stop cost.

    The four things N4 needs from one drive: the agent's exit code, the wall
    clock of the shutdown, the pids of the tree, and the node file afterwards.
    Returned rather than asserted so a test can choose what it is claiming.
    """
    root = tmp_path / "syncthing"
    (root / "jobs" / "job-1").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    monkeypatch.setenv("SYNCTHING_ROOT", str(root))

    tree = tree_script(refuse_polite=refuse_polite)
    _claim(root, tree.command)

    agent = Agent(
        settings_for(root, AGENT_SHUTDOWN_GRACE_S=str(shutdown_grace_s)),
        watcher=RecordingWatcher(resolve_paths(root, "node-01")),
    )
    stop = asyncio.Event()
    agent._stop = stop
    running = asyncio.create_task(agent.run())
    recorded: list[int] = []
    try:
        await wait_until(
            lambda: agent.supervisor is not None,
            "the supervisor never started",
            timeout=TREE_TIMEOUT_S,
        )
        await agent._maybe_claim_job(root / "jobs" / "job-1" / "state.yaml")
        await wait_until(
            lambda: len(tree.recorded()) >= tree.expected_pids,
            f"the tree never started; pids file {tree.pids_file}",
            timeout=TREE_TIMEOUT_S,
        )
        recorded = tree.recorded()
        started = time.monotonic()
        stop.set()
        exit_code = await asyncio.wait_for(running, timeout=TREE_TIMEOUT_S)
        elapsed = time.monotonic() - started
    finally:
        if not running.done():
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        for pid in recorded:
            if psutil.pid_exists(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass
    node_file = (root / "nodes" / "node-01.yaml").read_text(encoding="utf-8")
    return exit_code, elapsed, recorded, node_file


@pytest.mark.parametrize("shutdown_grace_s", [0.5, 1.0])
async def test_a_shutdown_never_exceeds_the_configured_grace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tree_script: Callable[..., Tree],
    shutdown_grace_s: float,
) -> None:
    """N4: a legal ``AGENT_SHUTDOWN_GRACE_S`` is a bound, at every legal size.

    Measured before the fix at 1.0 s: a 5.03 s shutdown, 5x over, because the
    execution task's own ``STOP_GRACE_S`` was spent inside ``_stop_tree``
    before ``wait_for`` around the task could cancel it. The stop is now sized
    from the configured grace, so the promise AC-15 states -- *a single file
    cannot make the agent exceed its own documented grace* -- holds for a value
    the operator chose, not only for the default.
    """
    exit_code, elapsed, recorded, _node_file = await drive_a_shutdown(
        tmp_path,
        monkeypatch,
        tree_script,
        shutdown_grace_s=shutdown_grace_s,
    )

    assert exit_code == 0
    assert elapsed < shutdown_grace_s + 0.5, (
        f"a {shutdown_grace_s}s documented grace produced a {elapsed:.2f}s "
        "shutdown (issue #125 N4)"
    )
    assert recorded, "the tree never started, so nothing was measured"


@pytest.mark.parametrize("shutdown_grace_s", [0.5, 1.0])
async def test_a_shutdown_whose_tree_refuses_still_fits_the_configured_grace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tree_script: Callable[..., Tree],
    shutdown_grace_s: float,
) -> None:
    """N4's harder half: the stop has to *use* the budget it was given.

    A cooperative tree exits inside the first wait, so it never reaches the
    place the overrun happened. This one refuses the polite request, which is
    what forces the grace to be spent.
    """
    exit_code, elapsed, recorded, _node_file = await drive_a_shutdown(
        tmp_path,
        monkeypatch,
        tree_script,
        shutdown_grace_s=shutdown_grace_s,
        refuse_polite=True,
    )

    assert exit_code == 0
    assert elapsed < shutdown_grace_s + 0.5, (
        f"a tree that refused the stop overran a {shutdown_grace_s}s grace by "
        f"{elapsed:.2f}s (issue #125 N4)"
    )
    assert recorded


async def test_a_shutdown_with_a_job_in_flight_stops_the_tree_and_writes_the_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tree_script: Callable[..., Tree],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-13, driven through a real ``Agent.run`` with no signal involved.

    Asserts the four facts together, because each is separately satisfiable by
    a broken implementation: the shutdown returns inside the grace, it does
    **not** log "did not complete within grace period", the supervisor did not
    have to expire its grace, and every pid in the tree is gone.
    """
    root = tmp_path / "syncthing"
    (root / "jobs" / "job-1").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    monkeypatch.setenv("SYNCTHING_ROOT", str(root))

    tree = tree_script()
    _claim(root, tree.command)

    agent = Agent(
        settings_for(root), watcher=RecordingWatcher(resolve_paths(root, "node-01"))
    )
    stop = asyncio.Event()
    agent._stop = stop  # the injectable seam #93 built for exactly this

    recorded: list[int] = []
    with caplog.at_level("INFO"):
        running = asyncio.create_task(agent.run())
        try:
            await wait_until(
                lambda: agent.supervisor is not None,
                "the supervisor never started",
                timeout=TREE_TIMEOUT_S,
            )
            # The claim is driven directly rather than through the watcher:
            # RecordingWatcher has no emitter behind it, and what is under test
            # is the shutdown, not the event plumbing that would reach it.
            await agent._maybe_claim_job(root / "jobs" / "job-1" / "state.yaml")
            await wait_until(
                lambda: len(tree.recorded()) >= tree.expected_pids,
                f"the tree never started; pids file {tree.pids_file}",
                timeout=TREE_TIMEOUT_S,
            )
            recorded = tree.recorded()
            started = time.monotonic()
            stop.set()
            exit_code = await asyncio.wait_for(running, timeout=TREE_TIMEOUT_S)
            elapsed = time.monotonic() - started
        finally:
            if not running.done():
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running

    assert exit_code == 0
    assert agent.supervisor is not None
    assert agent.supervisor.grace_expired is False

    messages = [record.getMessage() for record in caplog.records]
    assert not any(
        "did not complete within grace period" in m for m in messages
    ), f"the execution task had the whole grace and did not use it: {messages}"
    assert any("stopping job job-1" in m for m in messages), messages

    for pid in recorded:

        def gone(watched: int = pid) -> bool:
            return not psutil.pid_exists(watched)

        await wait_until(gone, f"pid {pid} survived the shutdown")
        assert psutil.pid_exists(pid) is False

    # The job the operator can see, and the node that admits to running it.
    state = _read_state(root)
    assert state.status == JobStatus.FAILED
    assert state.error == "stopped:shutdown"
    assert state.node_id == "node-01"
    assert state.completed_at is not None

    node_file = root / "nodes" / "node-01.yaml"
    assert "current_job_id: null" in node_file.read_text(encoding="utf-8")

    assert elapsed < SHUTDOWN_GRACE_S + 1.0, (
        "a shutdown that stops a cooperative tree should return in well under "
        f"its own grace, not approach twice it: {elapsed:.2f}s"
    )


async def test_the_shutdown_grace_is_spent_once_not_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-13's second assertion, measured on the arguments rather than on time.

    ``_shutdown_sequence`` used to hand ``SHUTDOWN_GRACE_S`` to
    ``supervisor.shutdown`` and then hand *the same number again* to the
    execution-task wait, so the documented 30 seconds was a bound on up to 60.
    A wall-clock assertion cannot see that: the supervisor's own tasks are
    stop-aware and return almost immediately, so today the elapsed time looks
    fine and the bug is invisible. Recording the two budgets is what makes the
    defect mechanical.
    """
    root = tmp_path / "syncthing"
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    monkeypatch.setenv("SYNCTHING_ROOT", str(root))

    agent = Agent(
        settings_for(root), watcher=RecordingWatcher(resolve_paths(root, "node-01"))
    )
    budgets: list[float] = []

    from agent.supervisor import TaskSupervisor

    real_shutdown = TaskSupervisor.shutdown
    real_wait_for = asyncio.wait_for

    async def record_shutdown(self: TaskSupervisor, grace_s: float) -> list[Any]:
        budgets.append(grace_s)
        return await real_shutdown(self, grace_s)

    async def record_wait_for(awaitable: Any, timeout: float | None = None) -> Any:
        # Only the execution-task wait is interesting. The supervisor's own
        # tasks wait on their intervals, which are also below the grace in
        # these quick settings, so recording every timeout would swamp the
        # total with numbers that are not the budget.
        if awaitable is not None and awaitable is agent._execution_task:
            budgets.append(timeout or 0.0)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(TaskSupervisor, "shutdown", record_shutdown)
    monkeypatch.setattr(asyncio, "wait_for", record_wait_for)

    stop = asyncio.Event()
    agent._stop = stop
    running = asyncio.create_task(agent.run())
    await wait_until(
        lambda: agent.supervisor is not None and agent.supervisor.task_names != [],
        "the supervisor never started",
        timeout=TREE_TIMEOUT_S,
    )
    stop.set()
    await real_wait_for(running, timeout=TREE_TIMEOUT_S)

    assert budgets, "neither wait was recorded; the spy did not fire"
    assert sum(budgets) <= SHUTDOWN_GRACE_S + 0.01, (
        "the grace was spent more than once: "
        f"{budgets} against a {SHUTDOWN_GRACE_S}s budget"
    )


# --- helpers ---------------------------------------------------------------


def _claim(root: Path, command: str) -> None:
    """Write the ``state.yaml`` a submitter would have written.

    PENDING and unclaimed, because that is what ``claim_job`` accepts -- a job
    in flight is not something the agent invents, it claims one. The claim
    itself is driven through the production ``_maybe_claim_job`` so the test
    is about the shutdown rather than about the path that reaches it.
    """
    from datetime import datetime

    from shared.file_ops.yaml_utils import write_yaml
    from shared.schemas.job_spec import JobSpec
    from shared.schemas.job_state import JobState
    from shared.schemas.resources import Resources

    state = JobState(
        job_id="job-1",
        spec=JobSpec(
            name="shutdown-probe",
            command=command,
            working_dir=str(root),
            resources=Resources(gpus=0, cpus=1, memory_gb=1),
        ),
        status=JobStatus.PENDING,
        node_id=None,
        created_at=datetime(2026, 1, 1),
        retry_count=0,
    )
    write_yaml(str(root / "jobs" / "job-1" / "state.yaml"), state)


def _read_state(root: Path) -> Any:
    from shared.file_ops.yaml_utils import read_yaml
    from shared.schemas.job_state import JobState

    return read_yaml(str(root / "jobs" / "job-1" / "state.yaml"), JobState)
