"""
The supervised agent loop and its ordered shutdown.

``Agent.run`` is wiring: wait for the folder, start the watcher lazily, register
supervised tasks, wait for the stop event, then shut down in a fixed order. The
two seams in that order -- ``_hand_back_in_flight_job`` and
``_write_final_state`` -- are deliberate no-ops in #93: #103 fills the first and
# #100 the second, and having them exist now is what stops either from
*rewriting* the shutdown sequence later (issue #93 Q4).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Final

from agent.config import (
    EXIT_INTERNAL_ERROR,
    EXIT_OK,
    EXIT_SHUTDOWN_GRACE_EXPIRED,
    EXIT_WATCHER_DEAD,
    FOLDER_RETRY_INTERVAL_S,
    AgentSettings,
)
from agent.paths import AgentPaths, resolve_paths
from agent.supervisor import TaskSupervisor, SupervisorTask
from agent.watcher import FolderWatcher

logger = logging.getLogger(__name__)

EVENT_DRAIN_INTERVAL_S: Final[float] = 0.5
FOLDER_WATCH_TASK: Final[str] = "folder-watch"
WATCHER_LIVENESS_TASK: Final[str] = "watcher-liveness"
EVENT_DRAIN_TASK: Final[str] = "event-drain"


class Agent:
    """One worker agent, on one node."""

    def __init__(
        self,
        settings: AgentSettings,
        *,
        watcher: FolderWatcher | None = None,
        stop: asyncio.Event | None = None,
    ) -> None:
        self._settings = settings
        self._paths: AgentPaths = resolve_paths(
            settings.SYNCTHING_ROOT,
            settings.NODE_ID,
            settings.AGENT_STATE_DIR,
        )
        self._watcher = watcher if watcher is not None else FolderWatcher(self._paths)
        self._stop = stop
        self._supervisor: TaskSupervisor | None = None
        self._watcher_started = False
        self._exit_code = EXIT_OK

    @property
    def paths(self) -> AgentPaths:
        return self._paths

    @property
    def supervisor(self) -> TaskSupervisor | None:
        """The task supervisor, once :meth:`run` has created it."""
        return self._supervisor

    async def run(self) -> int:
        """Run until stopped, then return the process exit code."""
        if self._stop is None:
            self._stop = asyncio.Event()
        self._supervisor = TaskSupervisor(
            on_fatal=self._on_fatal, stop_event=self._stop
        )

        self._install_signal_handlers()

        logger.info(
            "agent starting: node=%s syncthing_root=%s",
            self._settings.NODE_ID,
            self._paths.root,
        )

        ready = await self._ensure_folder()
        # Start the watcher before registering tasks that probe it. A liveness
        # task registered against a watcher that has not started yet reports a
        # false "dead watcher" and takes the agent down for a condition it can
        # recover from by itself.
        started = False
        if ready:
            try:
                started = await self._ensure_watcher_started()
            except Exception:
                logger.exception(
                    "the Syncthing folder watcher did not start; folder-watch "
                    "will keep trying"
                )
        if started:
            self._register_watcher_tasks()
        else:
            # No folder, or a watcher that will not start yet: register
            # folder-watch only. It starts the watcher the moment it can, and
            # spawns the tasks that only make sense once one exists (issue #93
            # K10).
            self._supervisor.add_task(self._folder_watch_task())
        await self._supervisor.start()

        await self._stop.wait()
        return await self._shutdown_sequence()

    def _install_signal_handlers(self) -> None:
        """Route SIGTERM and SIGINT into the stop event.

        Installed here rather than in ``__init__`` because ``add_signal_handler``
        needs a running loop -- which is also what lets a test deliver a real
        signal to a real ``Agent.run()``.

        ``add_signal_handler`` is POSIX-and-main-thread only. Without a fallback
        the suite cannot run on Windows at all, so a failure here is logged once
        and the agent relies on the injectable stop event instead (issue #93
        R7).
        """
        loop = asyncio.get_running_loop()
        stop = self._stop
        assert stop is not None
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (
                NotImplementedError,
                RuntimeError,
                ValueError,
                OSError,
                AttributeError,
            ) as exc:
                logger.warning(
                    "signal handlers are unavailable for %s here (%s); the agent "
                    "will only stop on an explicit shutdown request",
                    sig.name,
                    exc,
                )
                return

    def _folder_watch_task(self) -> SupervisorTask:
        return SupervisorTask(
            name=FOLDER_WATCH_TASK,
            coro_factory=lambda _stop: self._check_folder(),
            interval_s=self._settings.FOLDER_WATCH_INTERVAL_S,
        )

    def _register_watcher_tasks(self) -> None:
        assert self._supervisor is not None
        self._supervisor.add_task(self._folder_watch_task())
        self._supervisor.add_task(
            SupervisorTask(
                name=WATCHER_LIVENESS_TASK,
                coro_factory=lambda _stop: self._probe_watcher(),
                interval_s=self._settings.WATCHER_LIVENESS_INTERVAL_S,
                critical=True,
            )
        )
        self._supervisor.add_task(
            SupervisorTask(
                name=EVENT_DRAIN_TASK,
                coro_factory=lambda _stop: self._drain_events(),
                interval_s=EVENT_DRAIN_INTERVAL_S,
            )
        )

    async def _activate_watcher_tasks(self) -> None:
        """Start the tasks that only make sense once a watcher exists."""
        if self._supervisor is None or self._watcher is None:
            return
        self._supervisor.spawn(
            SupervisorTask(
                name=WATCHER_LIVENESS_TASK,
                coro_factory=lambda _stop: self._probe_watcher(),
                interval_s=self._settings.WATCHER_LIVENESS_INTERVAL_S,
                critical=True,
            )
        )
        self._supervisor.spawn(
            SupervisorTask(
                name=EVENT_DRAIN_TASK,
                coro_factory=lambda _stop: self._drain_events(),
                interval_s=EVENT_DRAIN_INTERVAL_S,
            )
        )

    async def _probe_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.probe_liveness()

    async def _ensure_watcher_started(self) -> bool:
        """Start the watcher once. Reachable only while the flag is ``False``.

        A failure here is raised on purpose: the supervisor records it, logs it
        with a traceback, and retries on the next ``folder-watch`` tick, which
        is what makes a watcher that refuses to start a logged retry rather than
        a dead agent.
        """
        if self._watcher_started:
            return True
        assert self._watcher is not None
        self._watcher.start()
        self._watcher_started = True
        logger.info("watching %s and %s", self._paths.jobs_dir, self._paths.nodes_dir)
        return True

    async def _ensure_folder(self) -> bool:
        """Wait, bounded, for the Syncthing folder to be present and writable.

        Failing this is **not** fatal. The agent logs, gives up after
        ``FOLDER_RETRY_MAX_S`` and then idles forever, because an agent that
        crash-loops on a folder which has not finished replicating yet is the
        failure mode systemd restart policies cannot rescue. ``folder-watch``
        picks the folder up whenever it finally appears.
        """
        budget = self._settings.FOLDER_RETRY_MAX_S
        started = time.monotonic()
        first = True
        while True:
            reason = self._describe_folder_problem()
            if reason is None:
                logger.info("syncthing folder is ready: %s", self._paths.root)
                return True

            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                logger.error(
                    "giving up waiting for the Syncthing folder at %s after "
                    "%.1fs (%s); staying alive and idle, folder-watch will pick "
                    "it up if it appears",
                    self._paths.root,
                    budget,
                    reason,
                )
                return False

            if first:
                logger.error(
                    "Syncthing folder %s is not usable yet: %s",
                    self._paths.root,
                    reason,
                )
                first = False
            else:
                logger.warning(
                    "Syncthing folder %s still not usable: %s (%.1fs of the "
                    "%.1fs budget left)",
                    self._paths.root,
                    reason,
                    remaining,
                    budget,
                )
            await asyncio.sleep(min(FOLDER_RETRY_INTERVAL_S, remaining))

    def _describe_folder_problem(self) -> str | None:
        """Create the sub-directories if possible; say why the folder is unusable.

        A path that exists as a *file* raises ``FileExistsError`` out of
        ``mkdir(exist_ok=True)``, so it is reported by the ``OSError`` arm and
        there is no separate "exists but is not a directory" case
        (issue #93 QA D-H).
        """
        for directory in (self._paths.jobs_dir, self._paths.nodes_dir):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return f"cannot create {directory}: {exc}"
            if not os.access(directory, os.W_OK):
                return f"{directory} is not writable"
        return None

    async def _check_folder(self) -> None:
        """One ``folder-watch`` tick: pick up a folder that appeared late.

        This is the self-heal: a bind mount that dropped and came back, or a
        watcher that could not start the first time, gets another attempt here
        rather than needing the process restarted.
        """
        reason = self._describe_folder_problem()
        if reason is not None:
            logger.warning(
                "Syncthing folder %s still not usable: %s", self._paths.root, reason
            )
            return

        await self._ensure_watcher_started()
        await self._activate_watcher_tasks()

    async def _drain_events(self) -> None:
        """Log every queued event, and nothing more.

        This is the #97 boundary: the watcher observes, this logs, and parsing
        or routing a job file does not exist yet.
        """
        queue = self._watcher.events()
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            logger.info("observed %s %s", event.kind, event.path)

    async def _hand_back_in_flight_job(self) -> None:
        """Shutdown seam for #103. The agent starts no child process in #93."""
        logger.debug("no in-flight job to hand back")

    async def _write_final_state(self) -> None:
        """Shutdown seam for #100. Nothing durable is written in #93."""
        logger.debug("no durable state to write")

    def _on_fatal(self, task_name: str, exc: BaseException) -> None:
        logger.error("critical task %r failed", task_name, exc_info=exc)
        self._exit_code = (
            EXIT_WATCHER_DEAD
            if task_name == WATCHER_LIVENESS_TASK
            else EXIT_INTERNAL_ERROR
        )
        if self._stop is not None:
            self._stop.set()

    async def _shutdown_sequence(self) -> int:
        assert self._supervisor is not None
        supervisor = self._supervisor
        stop = self._stop
        assert stop is not None

        logger.info("shutdown: signalling %d task(s)", len(supervisor.task_names))
        stop.set()

        grace = self._settings.SHUTDOWN_GRACE_S
        logger.info("shutdown: awaiting in-flight tasks (grace=%.1fs)", grace)
        failures = await supervisor.shutdown(grace)
        if failures:
            logger.warning(
                "shutdown: %d task failure(s) were recorded during the run",
                len(failures),
            )

        await self._hand_back_in_flight_job()

        if self._watcher is not None:
            self._watcher.stop()

        await self._write_final_state()

        if supervisor.grace_expired:
            logger.error(
                "shutdown grace of %.1fs expired with tasks still running", grace
            )
            return EXIT_SHUTDOWN_GRACE_EXPIRED

        logger.info("shutdown: complete")
        return self._exit_code
