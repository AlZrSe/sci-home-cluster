"""The supervised loop: activation, folder waiting, and ordered shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Coroutine, Iterator

import pytest

from agent.config import (
    EXIT_OK,
    EXIT_SHUTDOWN_GRACE_EXPIRED,
    EXIT_WATCHER_DEAD,
    AgentSettings,
    build_settings,
)
from agent.loop import (
    EVENT_DRAIN_TASK,
    FOLDER_WATCH_TASK,
    WATCHER_LIVENESS_TASK,
    Agent,
)
from agent.paths import AgentPaths, resolve_paths
from agent.supervisor import SupervisorTask
from agent.tests.conftest import (
    GUARDED_SIGNALS,
    SIGNAL_GUARD,
    FakeObserver,
    RecordingWatcher,
    SignalSafetyGuard,
    wait_for,
    wait_until,
)
from agent.watcher import FolderEvent, WatcherDead

pytestmark = pytest.mark.unit


def settings_for(root: Path, **env: str) -> AgentSettings:
    return build_settings(
        ["--node-id", "node-01"],
        environ={"SYNCTHING_ROOT": str(root), **env},
    )


def quick_settings(root: Path) -> AgentSettings:
    return settings_for(
        root,
        AGENT_SHUTDOWN_GRACE_S="0.2",
        AGENT_FOLDER_WATCH_INTERVAL_S="0.05",
        AGENT_WATCHER_LIVENESS_INTERVAL_S="0.05",
        AGENT_FOLDER_RETRY_MAX_S="0.3",
    )


class OrderRecordingWatcher(RecordingWatcher):
    def __init__(self, paths: AgentPaths, order: list[str]) -> None:
        super().__init__(paths)
        self.order = order

    def stop(self) -> None:
        self.order.append("watcher.stop")
        super().stop()


class OrderRecordingAgent(Agent):
    def __init__(self, settings: AgentSettings, watcher: Any, order: list[str]) -> None:
        super().__init__(settings, watcher=watcher)
        self.order = order

    async def _hand_back_in_flight_job(self) -> None:
        self.order.append("hand_back")
        await super()._hand_back_in_flight_job()

    async def _write_final_state(self) -> None:
        self.order.append("write_state")
        await super()._write_final_state()


class DeadWatcher(RecordingWatcher):
    def is_alive(self) -> bool:
        return False


#: Dispositions that would *not* carry a signal to the agent. ``SIG_DFL`` makes
#: the kernel kill pytest outright, ``SIG_IGN`` drops it silently, and
#: ``default_int_handler`` turns SIGINT into a ``KeyboardInterrupt`` that
#: interrupts the whole session rather than failing a test. The last one is not
#: optional knowledge: asyncio's ``remove_signal_handler`` restores
#: ``default_int_handler`` for SIGINT and ``SIG_DFL`` for everything else, so a
#: SIG_DFL-only check sails straight past the defect that disarms SIGINT.
UNSAFE_DISPOSITIONS: tuple[Any, ...] = (
    None,
    signal.SIG_DFL,
    signal.SIG_IGN,
    signal.default_int_handler,
)


def loop_delivers_signals(loop: asyncio.AbstractEventLoop) -> bool:
    """Whether ``loop``'s class routes signals to callbacks of its own.

    Read-only on purpose. The obvious way to ask -- install a handler and take
    it back out with ``remove_signal_handler`` -- is not an undo: on POSIX that
    calls ``signal.signal(sig, SIG_DFL)``, so the "probe" disarms whatever the
    agent had already armed and the signal that follows kills pytest instead of
    reaching it (issue #114 D1/D2). Comparing the resolved implementation
    against the base class asks the same question with no side effect, and
    works on POSIX and Windows alike without a ``sys.platform`` branch (D3).
    """
    base = asyncio.BaseEventLoop.add_signal_handler
    return type(loop).add_signal_handler is not base


def deliver_signal_to_self(sig: Any) -> None:
    """Send ``sig`` to this process -- once we have checked something will catch it.

    The disposition is asserted here, immediately before the ``os.kill``, and
    not a line earlier: at ``SIG_DFL`` the kernel applies the default action,
    which kills *pytest* mid-file -- no traceback, no summary, exit 143. That
    is the failure mode issue #114 was filed for, and ``getsignal`` is the
    exact, non-heuristic test of "would anything catch this".

    ``None`` counts as disarmed too: ``signal.getsignal`` returns it for a
    disposition that Python never installed, and ``SIG_IGN`` drops the signal
    without a word. What counts as "armed" is narrower than "not ``SIG_DFL``"
    -- see :data:`UNSAFE_DISPOSITIONS`.
    """
    disposition = signal.getsignal(sig)
    assert disposition not in UNSAFE_DISPOSITIONS, (
        f"{sig.name} is at {disposition!r} rather than a handler this suite "
        "armed -- refusing to signal the pytest process, where SIG_DFL kills "
        "it outright and default_int_handler interrupts the whole session. A "
        "handler was disarmed after the agent armed it (issue #114)"
    )
    with SIGNAL_GUARD.expecting(int(sig)):
        os.kill(os.getpid(), sig)


#: Ceiling for "the agent stopped after the signal". Deliberately not longer:
#: a bigger budget hides loop starvation rather than surviving it (spec R5).
STOP_TIMEOUT_S: float = 5.0


@contextmanager
def spy_on_armed_signal_handlers(
    loop: asyncio.AbstractEventLoop,
) -> Iterator[list[int]]:
    """Record which of the agent's own signal handlers actually fired.

    ``Agent._install_signal_handlers`` arms ``stop.set`` for SIGTERM and
    SIGINT. Wrapping the callback at the loop boundary lets a test assert that
    *the signal* stopped the agent -- asserting only that the agent exited
    cleanly cannot tell a delivered signal apart from ``drive``'s ``stop.set()``
    fallback, so such a test passes either way and proves nothing (AC-6).

    The production method is untouched and still runs; only the loop boundary
    is wrapped, and it is restored on the way out.
    """
    fired: list[int] = []
    cls = type(loop)
    original = cls.add_signal_handler

    def spy(_loop: Any, sig: Any, callback: Callable[..., None], *args: Any) -> None:
        def wrapped(*inner: Any) -> None:
            fired.append(int(sig))
            callback(*args, *inner)

        original(_loop, sig, wrapped, *args)

    setattr(cls, "add_signal_handler", spy)
    try:
        yield fired
    finally:
        setattr(cls, "add_signal_handler", original)


async def drive(
    agent: Agent,
    sig: Any = None,
    settle: float = 0.15,
    until: Callable[[], bool] | None = None,
    message: str = "the agent never reached the awaited condition",
) -> int:
    """Run ``agent.run()``, deliver ``sig`` once handlers are in, return the code.

    ``until`` is the load-safe replacement for a fixed ``settle``: pass the
    property the test actually needs, and the agent is stopped once it holds
    rather than after a wall-clock budget a loaded machine can eat (issue #93
    QA F5).

    ``sig`` is genuinely *delivered* -- sent to this process -- and there is no
    capability probe in front of it. The old probe was the bug: it installed a
    no-op handler and removed it, which on POSIX leaves ``SIG_DFL`` where the
    agent's handler was, so the signal that followed killed pytest (issue
    #114). With ``sig=None`` the stop event is set directly instead, which is
    the fallback path the shutdown-ordering tests are about.
    """
    running = asyncio.ensure_future(agent.run())
    assert await wait_for(
        lambda: agent.supervisor is not None and bool(agent.supervisor.task_names)
    ), "the agent never registered its tasks"
    await asyncio.sleep(settle)
    if until is not None:
        await wait_until(until, message)

    supervisor = agent.supervisor
    assert supervisor is not None
    stop = supervisor.stop_event

    if sig is None:
        stop.set()
    else:
        deliver_signal_to_self(sig)
        stopped = await wait_for(lambda: stop.is_set(), timeout=STOP_TIMEOUT_S)
        if not stopped and not loop_delivers_signals(asyncio.get_running_loop()):
            # Asked and it did not land: this platform's loop has no signal
            # support at all (Windows). Report that by name rather than
            # passing a fallback stop off as a delivery, but only *after*
            # trying -- an up-front capability guess is what let this test
            # assert nothing for a year.
            stop.set()
            await asyncio.wait_for(running, STOP_TIMEOUT_S)
            pytest.skip(
                f"{sig.name} was delivered but no handler stopped the agent: "
                "this platform's event loop does not implement "
                "add_signal_handler, so signal delivery to a running agent is "
                "not covered here"
            )
        assert stopped, (
            f"the agent did not stop within {STOP_TIMEOUT_S}s of {sig.name} "
            "being delivered -- the handler never reached Agent.run()"
        )

    return await asyncio.wait_for(running, STOP_TIMEOUT_S)


async def make_agent(
    root: Path, order: list[str] | None = None, watcher_cls: Any = RecordingWatcher
) -> Agent:
    settings = quick_settings(root)
    paths = resolve_paths(settings.SYNCTHING_ROOT, settings.NODE_ID)
    if order is None:
        return Agent(settings, watcher=watcher_cls(paths))
    return OrderRecordingAgent(settings, OrderRecordingWatcher(paths, order), order)


# --- signal handling and ordered shutdown (AC-6) --------------------------


async def test_sigterm_triggers_orderly_shutdown(syncthing_root: Path) -> None:
    order: list[str] = []
    agent = await make_agent(syncthing_root, order)
    with spy_on_armed_signal_handlers(asyncio.get_running_loop()) as fired:
        assert await drive(agent, signal.SIGTERM) == EXIT_OK
    assert fired == [int(signal.SIGTERM)], (
        "the agent's own SIGTERM handler stopped it, not drive()'s stop.set() "
        f"fallback; fired={fired}"
    )
    assert order == ["hand_back", "watcher.stop", "write_state"]


async def test_sigint_triggers_orderly_shutdown(syncthing_root: Path) -> None:
    order: list[str] = []
    agent = await make_agent(syncthing_root, order)
    with spy_on_armed_signal_handlers(asyncio.get_running_loop()) as fired:
        assert await drive(agent, signal.SIGINT) == EXIT_OK
    assert fired == [int(signal.SIGINT)], (
        "the agent's own SIGINT handler stopped it, not drive()'s stop.set() "
        f"fallback; fired={fired}"
    )
    assert order == ["hand_back", "watcher.stop", "write_state"]


async def test_shutdown_sequence_runs_steps_in_order(syncthing_root: Path) -> None:
    order: list[str] = []
    agent = await make_agent(syncthing_root, order)
    assert await drive(agent) == EXIT_OK
    assert order == ["hand_back", "watcher.stop", "write_state"]


async def test_hand_back_in_flight_job_precedes_watcher_stop(
    syncthing_root: Path,
) -> None:
    order: list[str] = []
    agent = await make_agent(syncthing_root, order)
    await drive(agent)
    assert order.index("hand_back") < order.index("watcher.stop")


async def test_write_final_state_seam_is_called(syncthing_root: Path) -> None:
    order: list[str] = []
    agent = await make_agent(syncthing_root, order)
    await drive(agent)
    assert "write_state" in order


async def test_signal_handlers_fall_back_where_unsupported(
    syncthing_root: Path, monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    refused: list[Any] = []

    def refuse(*_a: Any, **_k: Any) -> None:
        refused.append(_a)
        raise NotImplementedError("add_signal_handler is not supported here")

    # Patch the class the running loop *resolves* to, not
    # asyncio.AbstractEventLoop. On Linux the loop is a
    # _UnixSelectorEventLoop, which overrides add_signal_handler in
    # asyncio.unix_events, so a patch on the base class is never reached: the
    # production code took the success path, nothing warned, and this test
    # failed with a bare "assert 0 == 1" instead of naming the real cause
    # (issue #114). On Windows the loop inherits the base implementation, and
    # ``type(loop)`` is still the right object to patch -- no platform branch.
    monkeypatch.setattr(
        type(asyncio.get_running_loop()), "add_signal_handler", refuse, raising=True
    )
    order: list[str] = []
    agent = await make_agent(syncthing_root, order)
    with caplog.at_level(logging.WARNING, logger="agent.loop"):
        assert await drive(agent) == EXIT_OK

    assert refused, (
        "the patch never landed: add_signal_handler was not the method the "
        "running loop resolved to, so this test asserted nothing about the "
        "fallback"
    )
    warnings = [r for r in caplog.records if "signal handlers" in r.getMessage()]
    assert len(warnings) == 1, "the fallback warns once, not once per signal"
    assert order == ["hand_back", "watcher.stop", "write_state"]


# --- the signal safety guard itself (issue #114 D4) ------------------------


def test_signal_delivery_refuses_to_run_at_the_default_disposition(
    monkeypatch: Any,
) -> None:
    """Layer 1, with the process never put at risk: ``os.kill`` is a recorder."""
    killed: list[Any] = []

    def record_kill(pid: int, sig: Any) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(os, "kill", record_kill)

    for unsafe in UNSAFE_DISPOSITIONS:
        monkeypatch.setattr(signal, "getsignal", lambda _sig, _d=unsafe: _d)
        with pytest.raises(AssertionError, match="SIG_DFL"):
            deliver_signal_to_self(signal.SIGTERM)
        assert killed == [], "the guard must refuse *before* the signal is sent"


def test_signal_delivery_sends_the_signal_once_a_handler_is_armed(
    monkeypatch: Any,
) -> None:
    """The other half of the same assertion: a handler means the kill happens."""
    killed: list[Any] = []

    def handler(*_a: Any) -> None:
        return None

    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(signal, "getsignal", lambda _sig: handler)

    deliver_signal_to_self(signal.SIGTERM)
    assert killed == [(os.getpid(), signal.SIGTERM)]


def test_the_safety_guard_counts_only_signals_nobody_asked_for() -> None:
    guard = SignalSafetyGuard()
    with guard.expecting(int(signal.SIGTERM)):
        guard.record(int(signal.SIGTERM))
    assert guard.take_recorded() == [], "a deliberate delivery is not a stray"

    guard.record(int(signal.SIGINT))
    guard.record(int(signal.SIGINT))
    assert guard.take_recorded() == [int(signal.SIGINT), int(signal.SIGINT)]
    assert guard.take_recorded() == [], "recording is drained, not accumulated"


def test_the_safety_guard_is_armed_while_a_test_runs() -> None:
    """Layer 2 is live: a stray signal has somewhere to land, not a dead end."""
    for signum in GUARDED_SIGNALS:
        disposition = signal.getsignal(signum)
        assert disposition not in UNSAFE_DISPOSITIONS, (
            f"{signal.Signals(signum).name} is at {disposition!r} while a test "
            "is running; a stray signal there would kill or interrupt pytest"
        )


async def test_stop_is_safe_to_call_twice(syncthing_root: Path) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    watcher = RecordingWatcher(paths)
    agent = Agent(quick_settings(syncthing_root), watcher=watcher)
    running = asyncio.ensure_future(agent.run())
    assert await wait_for(
        lambda: bool(agent.supervisor and agent.supervisor.task_names)
    )
    stop = agent.supervisor.stop_event  # type: ignore[union-attr]
    stop.set()
    stop.set()
    assert await asyncio.wait_for(running, 5.0) == EXIT_OK


# --- containment (AC-7) and criticality (AC-8) ----------------------------


class FailingTaskAgent(Agent):
    def _register_watcher_tasks(self) -> None:
        super()._register_watcher_tasks()
        assert self._supervisor is not None
        self._supervisor.add_task(SupervisorTask("always-raises", self._boom, 0.05))

    @staticmethod
    async def _boom(_stop: asyncio.Event) -> None:
        raise RuntimeError("this tick failed")


async def test_raising_task_does_not_stop_the_agent_process(
    syncthing_root: Path,
) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    settings = quick_settings(syncthing_root)
    agent = FailingTaskAgent(settings, watcher=RecordingWatcher(paths))
    assert await drive(agent, settle=0.3) == EXIT_OK
    assert agent.supervisor is not None
    assert any(f.task_name == "always-raises" for f in agent.supervisor.failures)


async def test_dead_watcher_returns_exit_code_3(syncthing_root: Path) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    settings = quick_settings(syncthing_root)
    agent = Agent(settings, watcher=DeadWatcher(paths))
    assert await drive(agent, settle=0.4) == EXIT_WATCHER_DEAD


async def test_alive_watcher_does_not_exit(syncthing_root: Path) -> None:
    agent = await make_agent(syncthing_root)
    assert await drive(agent, settle=0.3) == EXIT_OK


async def test_probe_liveness_raises_through_the_supervisor(
    syncthing_root: Path,
) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    watcher = DeadWatcher(paths)
    assert watcher.is_alive() is False
    with pytest.raises(WatcherDead):
        watcher.probe_liveness()


# --- the no-crash-loop mechanism (AC-9) -----------------------------------


class ProbeCountingAgent(Agent):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.probe_times: list[float] = []

    def _describe_folder_problem(self) -> str | None:
        self.probe_times.append(time.monotonic())
        return super()._describe_folder_problem()


async def test_unwritable_root_logs_error_and_stays_alive(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a regular file where a folder should be", encoding="utf-8")

    agent = ProbeCountingAgent(quick_settings(blocker))
    with caplog.at_level(logging.INFO, logger="agent.loop"):
        running = asyncio.ensure_future(agent.run())
        assert await wait_for(
            lambda: agent.supervisor is not None and bool(agent.supervisor.task_names)
        )
        # Wait for the budget to be spent rather than sleeping 0.6 s and hoping.
        # The budget is 0.3 s of wall clock, and a loaded machine overruns it,
        # so the sleep was a bet rather than a bound (issue #93 QA F5).
        await wait_until(
            lambda: any("giving up waiting" in r.getMessage() for r in caplog.records),
            "the retry budget was never exhausted: "
            f"{[r.getMessage() for r in caplog.records]!r}",
        )

    assert not running.done(), "an unusable folder must not stop the agent"
    messages = [r.getMessage() for r in caplog.records]
    assert any("not usable yet" in m for m in messages)
    assert any("giving up waiting" in m for m in messages)

    assert agent.supervisor is not None
    assert agent.supervisor.task_names == [FOLDER_WATCH_TASK]

    agent.supervisor.stop_event.set()
    assert await asyncio.wait_for(running, 5.0) == EXIT_OK


async def test_folder_retry_is_bounded_and_backs_off(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("file", encoding="utf-8")
    agent = ProbeCountingAgent(settings_for(blocker, AGENT_FOLDER_RETRY_MAX_S="1.2"))

    started = time.monotonic()
    assert await agent._ensure_folder() is False
    elapsed = time.monotonic() - started

    gaps = [
        b - a for a, b in zip(agent.probe_times, agent.probe_times[1:], strict=False)
    ]
    assert elapsed >= 1.0, "the retry budget must actually be waited out"
    # Two probes is the floor, and it is the honest one: the third probe only
    # happens when the first 1 s sleep returns inside the remaining 0.2 s of
    # budget, so on a loaded machine -- where one `sleep(1.0)` overshoots by
    # more than 0.2 s -- the budget is gone after two probes and `>= 3` failed
    # for a retry loop behaving exactly as specified (issue #93 QA F5). Two is
    # guaranteed: the folder is probed once, then retried at least once. The
    # cadence, which is what actually distinguishes a retry from a spin, is
    # asserted against the spacing below.
    assert len(agent.probe_times) >= 2, "the folder is probed, then retried"
    assert len(agent.probe_times) <= 4, "retries are ~1s apart, not a tight loop"
    # The floor applies to the spacing *between* retries only. The final sleep
    # is `min(FOLDER_RETRY_INTERVAL_S, remaining)` by design, so it is however
    # much budget is left -- down to a few ms, which is well inside the
    # platform's timer granularity. Asserting a floor on it fails for the right
    # behaviour, intermittently (QA: 3 failures in 17 full-suite runs).
    assert gaps[0] >= 0.5, f"the first retry waits a real interval, not a spin: {gaps}"
    assert all(gap > 0.05 for gap in gaps[:-1]), gaps
    assert gaps[-1] >= 0, gaps
    assert elapsed < 2.0, "the final sleep is bounded by the remaining budget"


async def test_no_retry_after_budget_exhausted(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked-2"
    blocker.write_text("file", encoding="utf-8")
    agent = ProbeCountingAgent(settings_for(blocker, AGENT_FOLDER_RETRY_MAX_S="0.3"))

    assert await agent._ensure_folder() is False
    probes_after_return = len(agent.probe_times)
    await asyncio.sleep(0.6)
    assert len(agent.probe_times) == probes_after_return


async def test_missing_subdirectories_are_created(tmp_path: Path) -> None:
    root = tmp_path / "fresh-root"
    root.mkdir()
    assert not (root / "jobs").exists()

    agent = Agent(quick_settings(root))
    assert await agent._ensure_folder() is True
    assert (root / "jobs").is_dir()
    assert (root / "nodes").is_dir()


async def test_watcher_start_failure_is_not_fatal(syncthing_root: Path) -> None:
    attempts = 0

    def exploding() -> Any:
        nonlocal attempts
        attempts += 1
        observer = FakeObserver()
        observer.start_error = OSError("inotify exhausted")
        return observer

    paths = resolve_paths(syncthing_root, "node-01")
    watcher = RecordingWatcher(paths)
    watcher._observer_factory = exploding  # type: ignore[assignment]
    settings = quick_settings(syncthing_root)
    agent = Agent(settings, watcher=watcher)

    assert (
        await drive(
            agent,
            until=lambda: attempts >= 2,
            message="folder-watch never retried the watcher start",
        )
        == EXIT_OK
    )
    assert attempts >= 2, "folder-watch retries on its next tick"
    assert agent.supervisor is not None
    assert agent.supervisor.task_names == [
        FOLDER_WATCH_TASK
    ], "no watcher means no liveness probe to fail"
    assert any(f.task_name == FOLDER_WATCH_TASK for f in agent.supervisor.failures)


# --- lazy activation (AC-12, K10) -----------------------------------------


async def test_no_liveness_task_without_a_folder(tmp_path: Path) -> None:
    blocker = tmp_path / "no-folder"
    blocker.write_text("file", encoding="utf-8")
    agent = Agent(quick_settings(blocker))

    running = asyncio.ensure_future(agent.run())
    assert await wait_for(
        lambda: agent.supervisor is not None and bool(agent.supervisor.task_names)
    )
    assert agent.supervisor is not None
    assert agent.supervisor.task_names == [FOLDER_WATCH_TASK]

    agent.supervisor.stop_event.set()
    assert await asyncio.wait_for(running, 5.0) == EXIT_OK


async def test_all_three_tasks_are_registered_when_ready(
    syncthing_root: Path,
) -> None:
    agent = await make_agent(syncthing_root)
    running = asyncio.ensure_future(agent.run())
    assert await wait_for(
        lambda: agent.supervisor is not None and len(agent.supervisor.task_names) == 3
    )
    assert agent.supervisor is not None
    assert set(agent.supervisor.task_names) == {
        FOLDER_WATCH_TASK,
        WATCHER_LIVENESS_TASK,
        EVENT_DRAIN_TASK,
    }
    agent.supervisor.stop_event.set()
    assert await asyncio.wait_for(running, 5.0) == EXIT_OK


async def test_late_appearing_folder_activates_the_rest(tmp_path: Path) -> None:
    """K10: a watcher started late must still get a liveness probe."""
    root = tmp_path / "late-root"
    blocker = root.with_suffix(".blocked")
    blocker.write_text("file", encoding="utf-8")

    agent = Agent(quick_settings(blocker))
    running = asyncio.ensure_future(agent.run())
    assert await wait_for(
        lambda: agent.supervisor is not None
        and agent.supervisor.task_names == [FOLDER_WATCH_TASK]
    )

    blocker.unlink()
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)

    assert await wait_for(
        lambda: agent.supervisor is not None
        and WATCHER_LIVENESS_TASK in agent.supervisor.task_names
    ), "the liveness task must be spawned once the folder appears"

    assert agent.supervisor is not None
    agent.supervisor.stop_event.set()
    assert await asyncio.wait_for(running, 5.0) == EXIT_OK


async def test_watcher_starts_lazily_once_folder_appears(tmp_path: Path) -> None:
    root = tmp_path / "lazy-root"
    blocker = root.with_suffix(".blocked")
    blocker.write_text("file", encoding="utf-8")

    paths = resolve_paths(blocker, "node-01")
    watcher = RecordingWatcher(paths)
    started_observers: list[Any] = []
    original = watcher._observer_factory

    def recording() -> Any:
        observer = original()
        started_observers.append(observer)
        return observer

    watcher._observer_factory = recording  # type: ignore[assignment]
    agent = Agent(quick_settings(blocker), watcher=watcher)

    running = asyncio.ensure_future(agent.run())
    assert await wait_for(lambda: agent.supervisor is not None)
    assert started_observers == [], "nothing is watched before the folder exists"

    blocker.unlink()
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)

    assert await wait_for(lambda: bool(started_observers))
    assert started_observers[0].started is True

    assert agent.supervisor is not None
    agent.supervisor.stop_event.set()
    assert await asyncio.wait_for(running, 5.0) == EXIT_OK


async def test_folder_watch_starts_the_watcher_after_the_folder_appears(
    syncthing_root: Path,
) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    watcher = RecordingWatcher(paths)
    created: list[Any] = []
    original = watcher._observer_factory

    def recording() -> Any:
        observer = original()
        created.append(observer)
        return observer

    watcher._observer_factory = recording  # type: ignore[assignment]
    agent = Agent(quick_settings(syncthing_root), watcher=watcher)

    await agent._check_folder()

    assert len(created) == 1
    assert created[0].started is True
    assert agent._watcher_started is True

    await agent._check_folder()
    assert len(created) == 1, "the watcher is started once, not on every tick"


async def test_activate_watcher_tasks_is_a_no_op_before_start(
    syncthing_root: Path,
) -> None:
    agent = await make_agent(syncthing_root)
    await agent._activate_watcher_tasks()
    assert agent.supervisor is None


# --- exit code 4 (AC-6, Q4) ----------------------------------------------


class StubbornAgent(Agent):
    def _register_watcher_tasks(self) -> None:
        super()._register_watcher_tasks()
        assert self._supervisor is not None
        self._supervisor.add_task(SupervisorTask("stubborn", self._ignore_stop, 0.05))

    @staticmethod
    async def _ignore_stop(_stop: asyncio.Event) -> None:
        await asyncio.sleep(30)


async def test_shutdown_grace_expiry_returns_exit_code_4(syncthing_root: Path) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    settings = settings_for(syncthing_root, AGENT_SHUTDOWN_GRACE_S="0.05")
    agent = StubbornAgent(settings, watcher=RecordingWatcher(paths))
    assert await drive(agent, settle=0.2) == EXIT_SHUTDOWN_GRACE_EXPIRED
    assert agent.supervisor is not None
    assert agent.supervisor.grace_expired is True


# --- the #97 boundary -----------------------------------------------------


async def test_event_drain_logs_observed_events_without_parsing(
    syncthing_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = resolve_paths(syncthing_root, "node-01")
    watcher = RecordingWatcher(paths)
    agent = Agent(quick_settings(syncthing_root), watcher=watcher)

    watcher.emit("created", paths.jobs_dir / "job-1" / "state.yaml")
    watcher.emit("modified", paths.nodes_dir / "node-99.yaml")
    assert watcher.events().qsize() == 2

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        await agent._drain_events()

    messages = [r.getMessage() for r in caplog.records]
    assert any("created" in m and "job-1" in m for m in messages)
    assert any("modified" in m and "node-99.yaml" in m for m in messages)
    assert watcher.events().qsize() == 0, "every queued event is popped"


async def test_event_drain_is_safe_on_an_empty_queue(syncthing_root: Path) -> None:
    agent = await make_agent(syncthing_root)
    await agent._drain_events()


def test_event_drain_interval_is_fixed() -> None:
    from agent.loop import EVENT_DRAIN_INTERVAL_S

    assert isinstance(EVENT_DRAIN_INTERVAL_S, float)
    assert EVENT_DRAIN_INTERVAL_S > 0


async def test_paths_come_from_settings_not_the_environment(
    syncthing_root: Path,
) -> None:
    settings = quick_settings(syncthing_root)
    agent = Agent(settings)
    assert agent.paths.root == settings.SYNCTHING_ROOT
    assert agent.paths.state_dir == settings.AGENT_STATE_DIR


async def test_default_watcher_is_used_when_none_is_injected(
    syncthing_root: Path,
) -> None:
    from agent.watcher import FolderWatcher

    agent = Agent(quick_settings(syncthing_root))
    assert isinstance(agent._watcher, FolderWatcher)


def _unused(_coro: Coroutine[Any, Any, None]) -> None:
    return None


def test_folder_event_shape(tmp_path: Path) -> None:
    event = FolderEvent(kind="created", path=tmp_path / "a.yaml", observed_at=None)  # type: ignore[arg-type]
    assert event.kind == "created"
    assert event.path.name == "a.yaml"
