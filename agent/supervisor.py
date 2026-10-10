"""
The supervised loop's task contract.

Issue #97 adds job discovery, #96 adds heartbeat, #102 adds sampling. Each of
them should be one more :class:`SupervisorTask`, not another hand-written
process -- that is US-6, and it is what this module exists to deliver.

The whole contract is :func:`run_forever`: run one tick, swallow what the tick
threw, sleep until the stop event or the interval, repeat. A task that raises
does not take its siblings with it, because each is a separate ``asyncio.Task``
and nothing here propagates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Coroutine, NamedTuple

logger = logging.getLogger(__name__)

CoroFactory = Callable[[asyncio.Event], Coroutine[Any, Any, None]]


class TaskFailure(NamedTuple):
    """One recorded tick failure, with the time it happened."""

    task_name: str
    error: BaseException
    at: datetime


class SupervisorTask(NamedTuple):
    """A named unit of periodic work."""

    name: str
    coro_factory: CoroFactory
    interval_s: float
    critical: bool = False


async def _sleep_or_stop(interval_s: float, stop_event: asyncio.Event) -> None:
    """Wait out ``interval_s``, or return the moment the stop event fires.

    Sleeping on the stop event rather than on the clock is why ``shutdown`` is
    immediate: it never waits out a 60 s liveness interval.
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
    except (asyncio.TimeoutError, TimeoutError):
        return


async def run_forever(
    task: SupervisorTask,
    stop_event: asyncio.Event,
    *,
    on_fatal: Callable[[str, BaseException], None],
    failures: list[TaskFailure],
) -> None:
    """Run ``task`` on its interval until ``stop_event`` fires.

    ``CancelledError`` is re-raised and never recorded: cancellation is how the
    supervisor shuts a task down, not something that went wrong. Every other
    ``BaseException`` is contained -- a non-critical task records it and keeps
    ticking, a critical one reports it and stops, because a silently no-op
    agent is the worst outcome of this backlog and a failing liveness probe
    *is* the failure report.
    """
    while not stop_event.is_set():
        try:
            await task.coro_factory(stop_event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - containment is the contract
            if task.critical:
                on_fatal(task.name, exc)
                return
            failures.append(TaskFailure(task.name, exc, datetime.now(timezone.utc)))
            logger.exception("supervised task %r failed; continuing", task.name)
        await _sleep_or_stop(task.interval_s, stop_event)


class TaskSupervisor:
    """Owns the agent's periodic tasks and the ordered stop of all of them."""

    def __init__(
        self,
        *,
        on_fatal: Callable[[str, BaseException], None],
        on_shutdown: Callable[[], Awaitable[None]] | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._on_fatal = on_fatal
        self._on_shutdown = on_shutdown
        self._specs: dict[str, SupervisorTask] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._failures: list[TaskFailure] = []
        # The owner supplies its own event so that setting "stop" once -- for a
        # SIGTERM, for a fatal task, or for the shutdown sequence -- reaches the
        # tasks and the thing waiting on them at the same time.
        self._stop = stop_event if stop_event is not None else asyncio.Event()
        self._started = False
        self._grace_expired = False

    @property
    def stop_event(self) -> asyncio.Event:
        return self._stop

    @property
    def failures(self) -> list[TaskFailure]:
        return list(self._failures)

    @property
    def task_names(self) -> list[str]:
        return list(self._specs)

    @property
    def grace_expired(self) -> bool:
        """True when ``shutdown`` had to cancel a task that ignored the stop."""
        return self._grace_expired

    def add_task(self, task: SupervisorTask) -> None:
        """Register a task. Only valid before :meth:`start`."""
        if self._started:
            raise RuntimeError(
                f"add_task({task.name!r}) after start(); use spawn() once the "
                "supervisor is running"
            )
        self._specs[task.name] = task

    def spawn(self, task: SupervisorTask) -> None:
        """Register *and start* a task after :meth:`start`.

        Late activation is why this exists. The watcher is started lazily, the
        first time ``folder-watch`` sees the folder; without ``spawn`` a folder
        appearing after startup would start a watcher that never gets a
        ``watcher-liveness`` probe, so a watcher that then dies goes unnoticed
        (issue #93 K10).
        """
        if not self._started:
            raise RuntimeError(
                f"spawn({task.name!r}) before start(); use add_task() instead"
            )
        if task.name in self._tasks or task.name in self._specs:
            return
        self._specs[task.name] = task
        self._launch(task)

    def _launch(self, task: SupervisorTask) -> None:
        running = asyncio.get_running_loop().create_task(
            run_forever(
                task,
                self._stop,
                on_fatal=self._on_fatal,
                failures=self._failures,
            ),
            name=task.name,
        )
        self._tasks[task.name] = running
        running.add_done_callback(self._forget)

    def _forget(self, task: asyncio.Task[None]) -> None:
        """Drop a finished task from the running set but keep it registered.

        A critical task that reported and returned is finished but not
        forgotten: re-spawning it under the same name must stay a no-op, so a
        name can never appear twice in :attr:`task_names`.
        """
        name = task.get_name()
        if self._tasks.get(name) is task:
            del self._tasks[name]

    async def start(self) -> None:
        self._started = True
        for task in list(self._specs.values()):
            if task.name not in self._tasks:
                self._launch(task)

    async def shutdown(self, grace_s: float) -> list[TaskFailure]:
        """Stop every task, wait up to ``grace_s``, then cancel the stragglers."""
        self._grace_expired = False
        self._stop.set()
        if self._on_shutdown is not None:
            await self._on_shutdown()

        running = list(self._tasks.values())
        if not running:
            return self.failures

        _done, pending = await asyncio.wait(running, timeout=grace_s)
        if pending:
            self._grace_expired = True
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        return self.failures
