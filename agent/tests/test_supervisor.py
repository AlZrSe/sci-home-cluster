"""The supervised loop's contract: containment, criticality, and ordered stop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from agent.supervisor import TaskFailure, TaskSupervisor, SupervisorTask
from agent.tests.conftest import wait_until

pytestmark = pytest.mark.unit


def make_task(
    name: str,
    calls: list[str],
    *,
    interval: float = 0.01,
    raises: BaseException | None = None,
    critical: bool = False,
    ignore_stop: bool = False,
) -> SupervisorTask:
    async def body(_stop: asyncio.Event) -> None:
        calls.append(name)
        if ignore_stop:
            await asyncio.sleep(30)
        if raises is not None:
            raise raises

    return SupervisorTask(
        name=name, coro_factory=body, interval_s=interval, critical=critical
    )


async def run_for(seconds: float) -> None:
    """A plain sleep. Only for tests whose property is time-based, not count-based."""
    await asyncio.sleep(seconds)


def ticked(calls: list[str], name: str, count: int = 2) -> bool:
    """Whether task ``name`` has run at least ``count`` times."""
    return calls.count(name) >= count


async def test_raising_task_does_not_stop_sibling_task() -> None:
    calls: list[str] = []
    fatals: list[tuple[str, BaseException]] = []
    supervisor = TaskSupervisor(on_fatal=lambda n, e: fatals.append((n, e)))
    supervisor.add_task(make_task("bad", calls, raises=RuntimeError("boom")))
    supervisor.add_task(make_task("good", calls))
    await supervisor.start()
    # Wait for the property instead of sleeping a fixed 0.15 s and hoping two
    # 10 ms ticks landed in it (issue #93 QA F5).
    await wait_until(
        lambda: ticked(calls, "good") and ticked(calls, "bad"),
        f"both tasks must keep ticking, got {calls}",
    )
    await supervisor.shutdown(0.2)

    assert calls.count("good") >= 2
    assert calls.count("bad") >= 2
    assert fatals == []


async def test_task_failure_is_recorded_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(make_task("bad", calls, raises=ValueError("tick failed")))
    with caplog.at_level(logging.ERROR, logger="agent.supervisor"):
        await supervisor.start()
        await run_for(0.1)
        await supervisor.shutdown(0.2)

    assert supervisor.failures
    failure = supervisor.failures[0]
    assert failure.task_name == "bad"
    assert isinstance(failure.error, ValueError)
    assert failure.at.tzinfo is not None

    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert records
    assert records[0].exc_info is not None, "logger.exception must carry a traceback"


async def test_cancellation_is_not_a_failure() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(make_task("slow", calls, interval=0.01))
    await supervisor.start()
    await run_for(0.05)
    await supervisor.shutdown(0.2)
    assert supervisor.failures == []


async def test_critical_task_failure_calls_on_fatal() -> None:
    calls: list[str] = []
    fatals: list[tuple[str, BaseException]] = []
    supervisor = TaskSupervisor(on_fatal=lambda n, e: fatals.append((n, e)))
    supervisor.add_task(
        make_task("watcher-liveness", calls, raises=RuntimeError("dead"), critical=True)
    )
    await supervisor.start()
    await run_for(0.1)
    await supervisor.shutdown(0.2)

    assert len(fatals) == 1
    assert fatals[0][0] == "watcher-liveness"
    assert (
        calls.count("watcher-liveness") == 1
    ), "a critical task returns after reporting"
    assert supervisor.failures == [], "a critical failure is not a recorded TaskFailure"


async def test_non_critical_failure_does_not_call_on_fatal() -> None:
    calls: list[str] = []
    fatals: list[tuple[str, BaseException]] = []
    supervisor = TaskSupervisor(on_fatal=lambda n, e: fatals.append((n, e)))
    supervisor.add_task(make_task("folder-watch", calls, raises=OSError("nope")))
    await supervisor.start()
    await wait_until(
        lambda: len(supervisor.failures) >= 2,
        "a non-critical task must keep ticking and keep recording failures",
    )
    await supervisor.shutdown(0.2)
    assert fatals == []
    assert len(supervisor.failures) >= 2


async def test_liveness_task_is_the_only_critical_one() -> None:
    from agent.loop import (
        EVENT_DRAIN_TASK,
        FOLDER_WATCH_TASK,
        WATCHER_LIVENESS_TASK,
    )

    critical = {
        FOLDER_WATCH_TASK: False,
        WATCHER_LIVENESS_TASK: True,
        EVENT_DRAIN_TASK: False,
    }
    assert critical[WATCHER_LIVENESS_TASK] is True
    assert [name for name, flag in critical.items() if flag] == [WATCHER_LIVENESS_TASK]


async def test_base_exception_in_task_is_isolated() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(
        make_task("odd", calls, raises=KeyboardInterrupt("not a cancellation"))
    )
    supervisor.add_task(make_task("sibling", calls))
    await supervisor.start()
    await wait_until(
        lambda: ticked(calls, "sibling"),
        f"the sibling must survive a BaseException, got {calls}",
    )
    await supervisor.shutdown(0.2)

    assert calls.count("sibling") >= 2
    assert any(isinstance(f.error, KeyboardInterrupt) for f in supervisor.failures)


async def test_shutdown_sets_stop_event_and_returns_immediately() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(make_task("slow", calls, interval=30.0))
    await supervisor.start()
    await run_for(0.05)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await supervisor.shutdown(0.5)
    elapsed = loop.time() - started

    assert supervisor.stop_event.is_set()
    assert elapsed < 0.4, "a 30s interval must not be waited out"
    assert len(calls) == 1, "one tick, then a sleep on the stop event"


async def test_shutdown_returns_failures_and_marks_grace_expiry() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(make_task("bad", calls, raises=RuntimeError("boom")))
    supervisor.add_task(make_task("stubborn", calls, ignore_stop=True))
    await supervisor.start()
    await run_for(0.05)

    failures = await supervisor.shutdown(0.1)

    assert failures
    assert supervisor.grace_expired is True


async def test_shutdown_without_grace_expiry() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(make_task("polite", calls, interval=0.01))
    await supervisor.start()
    await run_for(0.05)
    await supervisor.shutdown(0.5)
    assert supervisor.grace_expired is False


async def test_add_task_after_start_raises() -> None:
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    await supervisor.start()
    with pytest.raises(RuntimeError) as excinfo:
        supervisor.add_task(SupervisorTask("late", lambda _s: None, 1.0))  # type: ignore[arg-type,return-value]
    assert "spawn" in str(excinfo.value)


async def test_spawn_before_start_raises() -> None:
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    with pytest.raises(RuntimeError) as excinfo:
        supervisor.spawn(
            SupervisorTask("early", lambda _s: _noop(), 1.0)  # type: ignore[arg-type,return-value]
        )
    assert "add_task" in str(excinfo.value)


async def test_spawn_of_a_running_task_is_a_no_op() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    await supervisor.start()
    task = make_task("extra", calls)
    supervisor.spawn(task)
    supervisor.spawn(task)
    await wait_until(
        lambda: ticked(calls, "extra"),
        f"the spawned task must keep running, got {calls}",
    )
    await supervisor.shutdown(0.2)

    assert supervisor.task_names.count("extra") == 1
    assert calls.count("extra") >= 2, "the task must keep running, not restart"


async def test_spawn_does_not_duplicate_a_registered_name() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    supervisor.add_task(make_task("registered", calls))
    await supervisor.start()
    supervisor.spawn(make_task("registered", calls))
    await run_for(0.1)
    await supervisor.shutdown(0.2)

    assert supervisor.task_names == ["registered"]


async def test_spawn_starts_a_task_that_did_not_exist_at_start() -> None:
    calls: list[str] = []
    supervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    await supervisor.start()
    assert supervisor.task_names == []
    supervisor.spawn(make_task("late", calls))
    await wait_until(
        lambda: ticked(calls, "late"),
        f"a task spawned after start() must run, got {calls}",
    )
    await supervisor.shutdown(0.2)
    assert calls.count("late") >= 2


async def test_on_shutdown_runs_before_tasks_are_awaited() -> None:
    order: list[str] = []

    async def on_shutdown() -> None:
        order.append("on_shutdown")

    supervisor = TaskSupervisor(on_fatal=lambda *_a: None, on_shutdown=on_shutdown)
    await supervisor.start()
    await supervisor.shutdown(0.2)
    assert order == ["on_shutdown"]


def test_task_failure_is_a_named_tuple() -> None:
    failure = TaskFailure("name", ValueError("x"), None)  # type: ignore[arg-type]
    assert failure.task_name == "name"
    assert isinstance(failure, tuple)


async def _noop() -> None:
    return None


def _coro_factory(_stop: asyncio.Event) -> Any:
    return _noop()


async def test_shutdown_with_no_tasks_is_safe() -> None:
    supervisor: TaskSupervisor = TaskSupervisor(on_fatal=lambda *_a: None)
    await supervisor.start()
    assert await supervisor.shutdown(0.1) == []


def test_supervisor_task_defaults_to_non_critical() -> None:
    task: Any = SupervisorTask("x", _coro_factory, 1.0)
    assert task.critical is False
