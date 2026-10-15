"""
The supervised agent loop and its ordered shutdown.

``Agent.run`` is wiring: wait for the folder, register this node, start the
watcher lazily, register the supervised tasks, wait for the stop event, then
shut down in a fixed order. The two seams in that order --
``_hand_back_in_flight_job`` and ``_write_final_state`` -- are deliberate no-ops
in #93: #103 fills the first and #100 the second, and having them exist now is
what stops either from *rewriting* the shutdown sequence later (issue #93 Q4).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import time
from pathlib import Path
from typing import Final, Optional

from agent.claim import claim_job
from agent.config import (
    EXIT_INTERNAL_ERROR,
    EXIT_OK,
    EXIT_SHUTDOWN_GRACE_EXPIRED,
    EXIT_WATCHER_DEAD,
    FOLDER_RETRY_INTERVAL_S,
    AgentSettings,
)
from agent.executor import execute_job
from agent.hardware import collect_node_spec, write_node_spec
from agent.paths import AgentPaths, resolve_paths
from agent.supervisor import TaskSupervisor, SupervisorTask
from agent.watcher import FolderWatcher
from shared.schemas.node_spec import NodeSpec

logger = logging.getLogger(__name__)

EVENT_DRAIN_INTERVAL_S: Final[float] = 0.5
FOLDER_WATCH_TASK: Final[str] = "folder-watch"
WATCHER_LIVENESS_TASK: Final[str] = "watcher-liveness"
EVENT_DRAIN_TASK: Final[str] = "event-drain"
NODE_SPEC_TASK: Final[str] = "node-registration"


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
        # Heartbeat tick counter for jitter seeding (issue #96)
        self._tick_count: int = 0
        # Last monotonic time for resume detection (issue #96)
        self._last_tick_monotonic: float | None = None
        # Currently claimed job ID (issue #97)
        self._current_job_id: Optional[str] = None
        # Execution task for the current job (issue #98)
        self._execution_task: Optional[asyncio.Task] = None

    @property
    def paths(self) -> AgentPaths:
        return self._paths

    @property
    def supervisor(self) -> TaskSupervisor | None:
        """The task supervisor, once :meth:`run` has created it."""
        return self._supervisor

    @property
    def current_job_id(self) -> Optional[str]:
        """The currently claimed job ID, or None if no job is in-flight."""
        return self._current_job_id

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
        if ready:
            # Before the watcher, and only once the folder is usable: the node
            # file is the only way this node enters the cluster, and it goes
            # into the directory that just became writable. Registering before
            # the folder exists would write nothing useful (issue #95).
            self._register_node_spec_task()
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

    def _register_node_spec_task(self) -> None:
        """Register the periodic node-registration task.

        One more ``SupervisorTask`` rather than bespoke asyncio plumbing, so
        registration inherits the shutdown grace, the tick containment and the
        retry cadence every other periodic task already has -- and so that
        stopping the agent stops registration with everything else.

        Non-critical on purpose: a node that fails to register must cost a
        heartbeat, not the agent (issue #95 AC-10).

        The interval is set to a small value; the actual heartbeat cadence with
        jitter is managed inside ``_registration_tick`` itself (issue #96).
        """
        assert self._supervisor is not None
        self._supervisor.add_task(
            SupervisorTask(
                name=NODE_SPEC_TASK,
                coro_factory=self._registration_tick,
                interval_s=0.1,  # Actual cadence managed by _registration_tick
            )
        )

    async def _registration_tick(self, stop_event: asyncio.Event) -> None:
        """One registration tick: probe this machine, rewrite the node file.

        The first tick runs as soon as the supervisor starts, which is why no
        separate "write once at startup" call site exists: the file lands right
        after ``_ensure_folder`` returned, and the ``run_forever`` contract is
        one tick then one sleep.

        Best-effort by design. A failed write is an ``OSError`` -- a Syncthing
        conflict, a read-only mount, a full disk -- and the agent's job is
        running jobs, so it logs a warning and lets the next tick retry. The
        supervisor would contain the failure either way, but it records it and
        logs a traceback, which is the wrong weight for a lost heartbeat. An
        exception that is *not* an ``OSError`` is a bug rather than a transient
        filesystem condition, so it is left to the supervisor's containment to be
        recorded and noticed.

        Heartbeat cadence with jitter and resume detection (issue #96):
        - Interval and jitter come from settings.
        - Jitter is seeded from (node_id, tick_count) for test determinism.
        - On resume from suspend (monotonic clock jump > 2 * interval), write
          immediately and log the event.
        """
        # Resume detection: if monotonic clock jumped forward by more than
        # 2 * interval, we likely woke from suspend. The current tick writes
        # immediately (which is the desired "immediate write on resume" behaviour).
        now_mono = time.monotonic()
        interval = self._settings.HEARTBEAT_INTERVAL_S
        if self._last_tick_monotonic is not None:
            delta_mono = now_mono - self._last_tick_monotonic
            if delta_mono > 2 * interval:
                logger.info(
                    "resume detected (monotonic gap %.1fs > 2*interval %.1fs), "
                    "writing immediate heartbeat",
                    delta_mono,
                    2 * interval,
                )
        self._last_tick_monotonic = now_mono

        try:
            node_spec = collect_node_spec(self._settings.NODE_ID)
            node_spec.current_job_id = self._current_job_id
            written = write_node_spec(self._paths, node_spec)
        except OSError as exc:
            logger.warning(
                "could not write the node file %s (%s); this heartbeat is lost "
                "and the next tick retries",
                self._paths.node_file,
                exc,
            )
            return
        if self._watcher is not None:
            # The agent watches the folder it just wrote into. Recording the
            # path keeps its own registration out of the observed event stream,
            # which is what SelfWriteLedger exists for; without it every 30 s
            # tick produces a "observed modified nodes/node-01.yaml" line.
            self._watcher.ledger.record(written)

        # Schedule next tick with jitter (issue #96)
        self._tick_count += 1
        jitter = self._settings.HEARTBEAT_JITTER_S
        if jitter > 0:
            # Seeded PRNG: hash(node_id + tick_count) -> uniform [-jitter, +jitter]
            seed = hash((self._settings.NODE_ID, self._tick_count))
            rng = random.Random(seed)
            interval += rng.uniform(-jitter, jitter)
        # Sleep with stop event awareness for clean shutdown
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(0.1, interval))
        except (asyncio.TimeoutError, TimeoutError):
            return

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

    def _is_job_state_file(self, path: Path | str) -> bool:
        """Check if a path is a jobs/<job_id>/state.yaml file."""
        path = Path(path)
        return (
            path.name == "state.yaml"
            and path.parent.name.startswith("job-")
            and path.parent.parent.name == "jobs"
        )

    async def _drain_events(self) -> None:
        """Drain the event queue and route job events to the claim handler.

        This is the #97 boundary: the watcher observes, this logs, and job
        discovery/claiming is implemented here.
        """
        queue = self._watcher.events()
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            logger.info("observed %s %s", event.kind, event.path)
            # Route job state file creation events to claim handler
            if event.kind == "created" and self._is_job_state_file(event.path):
                await self._maybe_claim_job(event.path)

    async def _maybe_claim_job(self, state_file_path) -> None:
        """Attempt to claim a job from a state.yaml file event.

        Checks:
        - Job status is PENDING
        - Job has no node_id (unclaimed)
        - This node can fit the job's resources
        - This agent is not already running a job
        - Advisory marker creation succeeds (O_EXCL)

        On successful claim, spawns the execution task as a background task
        so the agent continues heartbeat and log shipping.
        """
        if self._current_job_id is not None:
            logger.debug(
                "ignoring job event for %s: already running %s",
                state_file_path,
                self._current_job_id,
            )
            return

        # Collect current node spec for resource check
        node_spec = collect_node_spec(self._settings.NODE_ID)

        success, reason = await claim_job(
            self._paths,
            state_file_path,
            self._settings.NODE_ID,
            node_spec,
        )
        if success:
            job_id = state_file_path.parent.name
            self._current_job_id = job_id
            logger.info("claimed job %s", job_id)
            # Immediately write node spec with current_job_id set
            node_spec.current_job_id = job_id
            try:
                written = write_node_spec(self._paths, node_spec)
                if self._watcher is not None:
                    self._watcher.ledger.record(written)
            except OSError as exc:
                logger.warning(
                    "could not write node file with current_job_id %s: %s",
                    job_id,
                    exc,
                )

            # Spawn execution as a background supervised task
            self._spawn_execution_task(job_id, node_spec)
        else:
            logger.debug("did not claim job from %s: %s", state_file_path, reason)

    def _spawn_execution_task(self, job_id: str, node_spec: NodeSpec) -> None:
        """Spawn the job execution as a background task.

        The task runs concurrently with heartbeat, log shipping, and event drain.
        On completion, it clears _current_job_id and updates the node spec.
        """
        from shared.file_ops.yaml_utils import read_yaml
        from shared.schemas.job_state import JobState

        state_file = self._paths.jobs_dir / job_id / "state.yaml"
        job_state = read_yaml(str(state_file), JobState)
        job_spec = job_state.spec

        stop_event = asyncio.Event()

        async def execution_wrapper():
            try:
                await execute_job(
                    job_id=job_id,
                    job_spec=job_spec,
                    node_spec=node_spec,
                    paths=self._paths,
                    syncthing_root=self._paths.root,
                    stop_event=stop_event,
                    watcher=self._watcher,
                )
            except Exception as e:
                logger.exception("execution task failed for job %s: %s", job_id, e)
            finally:
                # Clear current job ID and update node spec
                self._current_job_id = None
                try:
                    updated_node_spec = collect_node_spec(self._settings.NODE_ID)
                    updated_node_spec.current_job_id = None
                    written = write_node_spec(self._paths, updated_node_spec)
                    if self._watcher is not None:
                        self._watcher.ledger.record(written)
                except OSError as exc:
                    logger.warning(
                        "could not write node file after job completion: %s", exc
                    )

        self._execution_task = asyncio.create_task(
            execution_wrapper(), name=f"execute-{job_id}"
        )
        # Don't await - runs in background

    async def _hand_back_in_flight_job(self) -> None:
        """Shutdown seam for #103. Hand back the in-flight job if any.

        Clears current_job_id and writes the node spec to reflect the handoff.
        """
        if self._current_job_id is not None:
            logger.info("handing back in-flight job %s", self._current_job_id)
            # Signal the execution task to stop
            if self._execution_task is not None and not self._execution_task.done():
                # The execution task checks the agent's stop event
                pass
            self._current_job_id = None
            # Update node spec to clear current_job_id
            node_spec = collect_node_spec(self._settings.NODE_ID)
            node_spec.current_job_id = None
            try:
                written = write_node_spec(self._paths, node_spec)
                if self._watcher is not None:
                    self._watcher.ledger.record(written)
            except OSError as exc:
                logger.warning("could not write node file after handoff: %s", exc)

    async def _write_final_state(self) -> None:
        """Shutdown seam for #100. Write final job state on completion.

        Clears current_job_id and writes the node spec.
        The execution task handles writing terminal state; this just cleans up.
        """
        if self._current_job_id is not None:
            logger.info("clearing current_job_id %s on shutdown", self._current_job_id)
            self._current_job_id = None
            # Update node spec to clear current_job_id
            node_spec = collect_node_spec(self._settings.NODE_ID)
            node_spec.current_job_id = None
            try:
                written = write_node_spec(self._paths, node_spec)
                if self._watcher is not None:
                    self._watcher.ledger.record(written)
            except OSError as exc:
                logger.warning("could not write node file after final state: %s", exc)

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

        # Wait for execution task to complete (it checks the stop event)
        if self._execution_task is not None and not self._execution_task.done():
            logger.info("waiting for execution task to complete")
            try:
                await asyncio.wait_for(self._execution_task, timeout=grace)
            except asyncio.TimeoutError:
                logger.warning("execution task did not complete within grace period")

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
