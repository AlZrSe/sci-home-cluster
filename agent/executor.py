"""
Job execution engine for the worker agent.

The platform-independent half of a job's life: validation, the child
environment, GPU allocation, log capture, the terminal state write, and the
error vocabulary that goes with it. Everything that differs between a Linux
box and a Windows box -- which shell runs the job, and how a process tree is
stopped -- lives in :mod:`agent.process` and is called from here; this module
must not grow any of it (issue #125).

Three things here were wrong before issue #125 and are load-bearing now:

* ``working_dir`` resolved against the agent's **current working directory**
  (defect F-b), so a job's directory was a function of where the supervisor
  happened to start. It is now decided on the raw string by
  :func:`resolve_working_dir`.
* the wait was a 100 ms ``proc.poll()`` loop around a stop event that nothing
  set, so the stop path was reachable only in theory (issue #125 §2).
* the CPU-affinity log line could not be formatted at all -- doubled
  conversion markers with three arguments -- so on Linux, the one platform
  where the call succeeds, ``logging`` dropped the record and wrote a
  traceback to stderr instead (defect F-a).

The names the platform half used to own -- the shell, the process-group
launch flag, the two process-group signals and the CPU-affinity syscall -- are
not spelled anywhere in this file, not even in a comment. That is deliberate:
they live in :mod:`agent.process`, and keeping the references out entirely
means the mechanical pin in ``agent/tests/test_execution.py`` can assert the
move with a plain substring search instead of a judgement call about prose.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Optional

from shared.file_ops.path_utils import is_within_syncthing_root, normalize_path
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec

from agent.log_writer import BoundedLineWriter
from agent.paths import AgentPaths
from agent.process import (
    STOP_GRACE_S,
    STOP_VERIFY_TIMEOUT_S,
    ProcessTree,
    StopReason,
    launch_flags,
    resolve_shell,
    shell_argv,
)
from agent.terminal import write_terminal_state
from agent.watcher import FolderWatcher

logger = logging.getLogger(__name__)

#: The job error vocabulary. Issue #100 built ``state.yaml``'s ``error`` field
#: for "a documented vocabulary of machine-readable codes"; this is that
#: vocabulary, and it is held next to the code that writes it so a code cannot
#: be invented in a branch and never recorded. ``error`` carries the
#: *provenance* -- whose fault it was -- and ``exit_code`` carries the number;
#: neither is derived from the other (issue #125 D8).
#:
#: ``exit_code`` is always the **real** wait status. ``-1`` is already the
#: backend's cancel sentinel (``job_service.py:339``) and ``0xC000013A``
#: (3221225786) is already what a Windows process killed by
#: ``CTRL_BREAK_EVENT`` returns, so a sentinel here would collide with two
#: meanings that exist (issue #125 Q5).
JOB_ERRORS: Final[dict[str, str]] = {
    "working_dir_not_found": "launch_failed:working_dir_not_found",
    "working_dir_create_failed": "launch_failed:working_dir_create_failed: {error}",
    "input_missing": "launch_failed:input_missing",
    "insufficient_gpus": "launch_failed:insufficient_gpus",
    "shell_not_found": "launch_failed:shell_not_found",
    "process_creation_failed": "launch_failed:process_creation_failed: {error}",
    "command_not_found": "launch_failed:command_not_found",
    "process_failed": "process_failed:exit_code={exit_code}",
    "stopped": "stopped:{reason}",
    "tree_survived": "stop_failed:tree_survived",
}

#: The job's "it never started" number. ``127`` is POSIX's "command could not
#: be found" and it is reused deliberately at the *job* level -- the job did
#: not start -- while ``error`` says which of the two faults it was. The two
#: have different owners and different fixes: an agent with no shell is a
#: misconfigured machine, and a missing command is a submitter's typo
#: (issue #125 Q5).
SHELL_NOT_FOUND_EXIT_CODE: Final[int] = 127


class ExecutionResult:
    """Result of a job execution."""

    def __init__(
        self,
        exit_code: Optional[int],
        error_code: Optional[str],
        stdout_path: str,
        stderr_path: str,
    ) -> None:
        self.exit_code = exit_code
        self.error_code = error_code
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path


# Internal variables that must be stripped from the child environment
INTERNAL_ENV_VARS = frozenset(
    {
        "SHARED_TOKEN",
        "SECRET_KEY",
        "SHC_STATE_DIR",
        "SYNCTHING_ROOT",
        "NODE_ID",
    }
)


def _is_internal_env_var(name: str) -> bool:
    """Check if an environment variable name matches internal patterns."""
    if name in INTERNAL_ENV_VARS:
        return True
    upper = name.upper()
    if upper.endswith("_TOKEN") or upper.endswith("_KEY"):
        return True
    return False


def _process_failed(exit_code: int | None) -> str:
    return JOB_ERRORS["process_failed"].format(exit_code=exit_code)


def _stopped(reason: StopReason) -> str:
    return JOB_ERRORS["stopped"].format(reason=reason)


def resolve_working_dir(working_dir: str, syncthing_root: Path) -> Path:
    """Resolve a job's ``working_dir``, decided on the **raw** string.

    Two branches and no third:

    * absolute *on this host* -> used as-is. What "absolute" means is the
      host's business, which is why this is ``Path.is_absolute()`` and not a
      string test for a leading slash;
    * not absolute on this host -> resolved against ``SYNCTHING_ROOT``, never
      against the current working directory.

    The second branch is what removes defect F-b. ``normalize_path`` calls
    ``.resolve()`` on a relative path, and after that the result reports
    ``is_absolute() == True``, so a CWD-relative job directory was
    indistinguishable from an absolute one *after* normalisation and the only
    place the distinction still exists is here (issue #125 Q3). The default
    ``JobSpec.working_dir`` of ``/sync/projects/new-run`` is the case that
    shows it: absolute on Linux, relative on Windows, where it becomes
    ``<SYNCTHING_ROOT>/sync/projects/new-run`` and is created.
    """
    candidate = Path(working_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    # On Windows a path like ``/sync/projects/new-run`` is **rooted but not
    # absolute**: it has no drive, so ``is_absolute()`` is False, and pathlib
    # treats a rooted right-hand operand as replacing everything to its left.
    # Joining it as-is would therefore produce ``C:\sync\projects\new-run`` --
    # outside the Syncthing root, on whatever drive the root happens to be on.
    # The anchor is stripped before the join; on POSIX a relative path has
    # none and this is a no-op.
    anchor = candidate.anchor
    if anchor:
        candidate = candidate.relative_to(anchor)
    return syncthing_root / candidate


def validate_and_prepare_working_dir(
    working_dir: str,
    syncthing_root: Path,
) -> tuple[Path, Optional[str]]:
    """
    Validate and prepare the working directory.

    Returns:
        (resolved_path, error_message). Error message is None on success.
    """
    resolved = normalize_path(resolve_working_dir(working_dir, syncthing_root))

    if not is_within_syncthing_root(resolved):
        # Outside Syncthing root: must exist, we don't create it
        if not resolved.exists():
            return resolved, JOB_ERRORS["working_dir_not_found"]
        return resolved, None

    # Inside Syncthing root: create parents if needed
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return resolved, JOB_ERRORS["working_dir_create_failed"].format(error=e)

    return resolved, None


def build_child_env(
    parent_env: dict[str, str],
    spec_env: dict[str, str],
    gpu_indices: list[int] | None = None,
) -> dict[str, str]:
    """
    Build the environment for the child process.

    - Starts with parent_env (os.environ)
    - Removes cluster-internal variables
    - Applies spec_env (overrides)
    - If gpu_indices provided, sets CUDA_VISIBLE_DEVICES
    - Logs variable NAMES only (never values) at INFO
    """
    child_env = dict(parent_env)

    # Remove internal variables
    internal_removed = []
    for key in list(child_env.keys()):
        if _is_internal_env_var(key):
            child_env.pop(key, None)
            internal_removed.append(key)

    # Apply spec environment (overrides)
    child_env.update(spec_env)

    # Set CUDA_VISIBLE_DEVICES if GPU indices provided
    if gpu_indices:
        child_env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in gpu_indices)

    # Log variable names at INFO (never values)
    all_vars = list(child_env.keys())
    logger.info("child environment variables: %s", sorted(all_vars))
    if internal_removed:
        logger.info(
            "stripped internal environment variables: %s", sorted(internal_removed)
        )

    return child_env


def _apply_cpu_affinity(job_id: str, pid: int, requested: int) -> None:
    """Pin ``pid`` to ``requested`` CPUs where the platform can, and say so.

    ``resources.cpus`` is **advisory**, and the defect was never the absence of
    a portable primitive -- there is none, the Linux-only syscall is Linux-only
    and a per-process affinity mask means different things on Linux and
    Windows -- it was the silence. The capability is *probed* rather than
    assumed, and both answers are logged at INFO or above so an operator
    reading a job's log knows whether ``cpus`` did anything (issue #125 Q6).

    Probing with ``getattr`` rather than naming the attribute is also the mypy
    fix: the bare reference raised ``AttributeError`` on every non-Linux node
    into a ``except (AttributeError, OSError)`` arm that logged at DEBUG, which
    is how ``cpus`` came to look enforced when it was not.

    The "more CPUs than this node has" check runs **before** the capability
    probe because it is true on every platform: a submitter asking for more
    cores than the machine owns is a mistake whatever the OS can do about it,
    and it used to be answered with an ``EINVAL`` into the swallowed arm.
    """
    available = os.cpu_count() or 0
    if requested > available:
        logger.warning(
            "resources.cpus=%d exceeds this node's %d CPUs; pinning to all of them",
            requested,
            available,
        )

    set_affinity = getattr(os, "sched_setaffinity", None)
    if set_affinity is None:
        logger.info(
            "resources.cpus=%d is advisory and not enforceable on this platform; "
            "the job sees all %d CPUs",
            requested,
            available,
        )
        return

    cpus = set(range(min(requested, available)))
    try:
        set_affinity(pid, cpus)
    except OSError as exc:
        logger.warning("could not set CPU affinity for job %s: %s", job_id, exc)
        return
    # The CPU set is rendered as a set literal on purpose: "CPUs {0, 1, 2}"
    # reads as the same thing on both platforms, where a range would not.
    logger.info("pinned job %s to CPUs %s (pid %d)", job_id, cpus, pid)


def _reader_thread(
    stream: Any,
    writer: BoundedLineWriter,
    stop_event: threading.Event,
) -> None:
    """Background thread reading lines from a process stream."""
    try:
        while not stop_event.is_set():
            line = stream.readline()
            if not line:  # EOF
                break
            writer.write_line(line)
    except Exception as e:
        logger.warning("log reader thread error: %s", e)


async def _stop_tree(
    tree: ProcessTree,
    job_id: str,
    exit_waiter: "asyncio.Task[int]",
) -> bool:
    """Escalate from polite to forced, then **verify**. ``True`` when it is gone.

    The escalation waits on the job's own exit rather than sleeping: a fixed
    sleep is a bet that the tree died by then, and the 100 ms poll loop it
    replaced was a race between the poll noticing and the timer firing.

    The two tiers are logged separately and differently on purpose. "Stopped
    after the graceful request" and "required the forced stop" are the
    difference between a job that checkpointed and one that had to be taken
    out, and an operator reading a stop needs to know which happened.
    """
    logger.info("stopping job %s: grace %.1fs", job_id, STOP_GRACE_S)
    tree.stop_gracefully()
    await asyncio.wait({exit_waiter}, timeout=STOP_GRACE_S)
    if exit_waiter.done():
        logger.info("job %s stopped after the graceful request", job_id)
        return True

    logger.warning("job %s required the forced stop", job_id)
    tree.stop_forced()
    gone = await asyncio.to_thread(tree.verify, STOP_VERIFY_TIMEOUT_S)
    if gone:
        logger.info("verified: 0 processes remain for job %s", job_id)
    else:
        logger.error(
            "job %s: the tree still has processes after %.1fs",
            job_id,
            STOP_VERIFY_TIMEOUT_S,
        )
    return gone


async def _fail_before_launch(
    *,
    job_id: str,
    paths: AgentPaths,
    error: str,
    exit_code: int | None,
    watcher: FolderWatcher | None,
) -> ExecutionResult:
    """Write the terminal state for a job that never had a process.

    ``exit_code`` is ``None`` for a stop that arrived before the launch: there
    was never a process, so there is no wait status to report and inventing
    one would be a sentinel the backend cannot tell from a real code
    (issue #125 §4 Q10).
    """
    await write_terminal_state(
        paths=paths,
        job_id=job_id,
        status=JobStatus.FAILED,
        exit_code=exit_code,
        error=error,
        watcher=watcher,
    )
    return ExecutionResult(
        exit_code=exit_code,
        error_code=error,
        stdout_path="",
        stderr_path="",
    )


async def execute_job(  # noqa: C901 - complex execution logic with many validation paths
    *,
    job_id: str,
    job_spec: JobSpec,
    node_spec: NodeSpec,
    paths: AgentPaths,
    syncthing_root: Path,
    stop_event: asyncio.Event,
    shell: str = "auto",
    watcher: FolderWatcher | None = None,
) -> ExecutionResult:
    """
    Execute a job and return ExecutionResult.

    The function:
    - Validates working_dir and paths.input
    - Builds the child environment
    - Applies resource requests (GPU enforced, CPU advisory and reported)
    - Launches the process through the **configured** shell, in its own
      stoppable process group -- see :func:`agent.process.shell_argv` for why
      that is one assembled string on Windows and a list on POSIX
    - Captures stdout/stderr to log files under jobs/<job_id>/logs/
    - Returns when the process exits, when ``stop_event`` is set (and the tree
      is stopped and *verified* gone), or when this task is cancelled (and the
      tree is stopped without a grace period, because a cancellation is
      already somebody's decision that this must stop now)

    ``shell`` is the already-resolved ``AGENT_SHELL`` value rather than the
    settings object: one string is trivially overridable in a test without
    building a settings model, and it keeps this module importable without
    :mod:`agent.config` (issue #125 §6.4.1).
    """
    # Validate working directory
    working_dir, error = validate_and_prepare_working_dir(
        job_spec.working_dir, syncthing_root
    )
    if error:
        logger.error("working_dir validation failed for %s: %s", job_id, error)
        return await _fail_before_launch(
            job_id=job_id,
            paths=paths,
            error=error,
            exit_code=1,
            watcher=watcher,
        )

    # Validate paths.input if specified
    if job_spec.paths.input:
        input_path = normalize_path(Path(job_spec.paths.input))
        # Check if input is within Syncthing root
        if not is_within_syncthing_root(input_path):
            # Outside Syncthing root: must exist
            if not input_path.exists():
                logger.error("input path missing for %s: %s", job_id, input_path)
                return await _fail_before_launch(
                    job_id=job_id,
                    paths=paths,
                    error=JOB_ERRORS["input_missing"],
                    exit_code=1,
                    watcher=watcher,
                )
        else:
            # Inside Syncthing root: check existence
            if not input_path.exists():
                logger.error("input path missing for %s: %s", job_id, input_path)
                return await _fail_before_launch(
                    job_id=job_id,
                    paths=paths,
                    error=JOB_ERRORS["input_missing"],
                    exit_code=1,
                    watcher=watcher,
                )

    # Build child environment
    # Allocate GPU indices: first N GPUs where N = job_spec.resources.gpus
    gpu_indices = None
    if job_spec.resources.gpus > 0:
        available_gpus = len(node_spec.gpus)
        if job_spec.resources.gpus <= available_gpus:
            gpu_indices = list(range(job_spec.resources.gpus))
        else:
            # This should not happen if claim logic worked correctly
            logger.error(
                "GPU allocation failed for %s: need %d, have %d",
                job_id,
                job_spec.resources.gpus,
                available_gpus,
            )
            return await _fail_before_launch(
                job_id=job_id,
                paths=paths,
                error=JOB_ERRORS["insufficient_gpus"],
                exit_code=1,
                watcher=watcher,
            )

    child_env = build_child_env(
        parent_env=dict(os.environ),
        spec_env=job_spec.env,
        gpu_indices=gpu_indices,
    )

    # A stop that arrives before the process exists is still a stop: the job is
    # claimed, so it needs a terminal state and a reason. Writing nothing here
    # would leave state.yaml: RUNNING with no process attached, which is
    # verbatim the state issue #103 exists to detect and repair.
    if stop_event.is_set():
        logger.info("stop requested before job %s was launched", job_id)
        return await _fail_before_launch(
            job_id=job_id,
            paths=paths,
            error=_stopped("shutdown"),
            exit_code=None,
            watcher=watcher,
        )

    # Resolve the shell before Popen, so "this machine has no shell" is a
    # fact the agent can report together with the PATH it searched, rather
    # than a FileNotFoundError whose message says nothing about which of the
    # two faults it was (issue #125 Q5).
    try:
        shell_spec = resolve_shell(shell)
    except ValueError as exc:
        logger.error("job %s: %s", job_id, exc)
        return await _fail_before_launch(
            job_id=job_id,
            paths=paths,
            error=JOB_ERRORS["shell_not_found"],
            exit_code=SHELL_NOT_FOUND_EXIT_CODE,
            watcher=watcher,
        )

    if shutil.which(shell_spec.program) is None:
        logger.error(
            "job %s: the configured shell %r (AGENT_SHELL=%r) is not on PATH %r; "
            "no process was launched",
            job_id,
            shell_spec.program,
            shell,
            os.environ.get("PATH", ""),
        )
        return await _fail_before_launch(
            job_id=job_id,
            paths=paths,
            error=JOB_ERRORS["shell_not_found"],
            exit_code=SHELL_NOT_FOUND_EXIT_CODE,
            watcher=watcher,
        )

    # Prepare log files
    timestamp = datetime.now().isoformat().replace(":", "-")
    logs_dir = paths.jobs_dir / job_id / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"stdout-{timestamp}.log"
    stderr_path = logs_dir / f"stderr-{timestamp}.log"

    stdout_writer = BoundedLineWriter(stdout_path)
    stderr_writer = BoundedLineWriter(stderr_path)

    # Launch the process
    command = job_spec.command
    logger.info("launching job %s: command=%r cwd=%s", job_id, command, working_dir)

    # One dict rather than a splat of a second dict: `launch_flags()` is the
    # single source of truth for "how does a job become its own stoppable
    # group", and `Any` here is what lets the two halves of it type-check
    # against `Popen`'s overloads at all -- neither keyword exists on the
    # other platform, so a statically spelled call could only be correct on
    # one of them.
    popen_kwargs: dict[str, Any] = {
        "cwd": working_dir,
        "env": child_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": False,  # Binary mode for undecodable handling
    }
    popen_kwargs.update(launch_flags())

    try:
        proc = subprocess.Popen(shell_argv(shell_spec, command), **popen_kwargs)
    except FileNotFoundError:
        # which() said yes and CreateProcess said no: a race, or a PATH only
        # this process can see. Still the agent's fault, still not the
        # submitter's command (issue #125 Q5).
        logger.error(
            "job %s: the shell %r disappeared between resolution and launch",
            job_id,
            shell_spec.program,
        )
        stdout_writer.close()
        stderr_writer.close()
        return await _fail_before_launch(
            job_id=job_id,
            paths=paths,
            error=JOB_ERRORS["shell_not_found"],
            exit_code=SHELL_NOT_FOUND_EXIT_CODE,
            watcher=watcher,
        )
    except Exception as e:
        logger.error("process creation failed for %s: %s", job_id, e)
        stdout_writer.close()
        stderr_writer.close()
        return await _fail_before_launch(
            job_id=job_id,
            paths=paths,
            error=JOB_ERRORS["process_creation_failed"].format(error=e),
            exit_code=1,
            watcher=watcher,
        )

    # Own the tree from here on: one stop handle, and the launch line says
    # which mechanism it is holding, so a degraded node is visible without
    # enabling DEBUG (issue #125 §4 Q2).
    tree = ProcessTree(proc, log=logger.info)
    logger.info(
        "launched job %s: shell=%s pid=%d tree_control=%s",
        job_id,
        shell_spec.program,
        proc.pid,
        tree.control_mode,
    )

    # Set CPU affinity in the child process (advisory, always reported)
    if job_spec.resources.cpus > 0:
        _apply_cpu_affinity(job_id, proc.pid, job_spec.resources.cpus)

    # Start reader threads for stdout/stderr
    stop_readers = threading.Event()
    stdout_thread = threading.Thread(
        target=_reader_thread,
        args=(proc.stdout, stdout_writer, stop_readers),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader_thread,
        args=(proc.stderr, stderr_writer, stop_readers),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    # Wait on both conditions at once: the job's exit, and the stop request.
    # The old 100 ms poll woke the event loop ten times a second for the
    # lifetime of every job and turned the escalation into a race between the
    # poll noticing and the timer firing; a wait on both removes the poll and
    # makes the escalation a wait with a deadline (issue #125 Q9).
    #
    # `subprocess.Popen` rather than `create_subprocess_exec` on purpose: the
    # executor needs `pid`, the launch flags and the job handle, all of which
    # are Popen's surface, and `asyncio.to_thread` works on every event loop
    # class -- whereas asyncio subprocess support is a property of the loop
    # (`ThreadedChildWatcher` vs `ProactorEventLoop`) that the agent already
    # fights over when `add_signal_handler` forces a Selector loop on POSIX.
    exit_waiter = asyncio.create_task(
        asyncio.to_thread(proc.wait), name=f"exit-{job_id}"
    )
    stop_waiter = asyncio.create_task(stop_event.wait(), name=f"stop-{job_id}")
    stop_reason: Optional[StopReason] = None
    tree_verified = True

    try:
        try:
            await asyncio.wait(
                {exit_waiter, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # Cancellation is *not* the stop event. Somebody has already
            # decided that this must stop now, so the forced tier runs with no
            # grace and the cancellation still propagates -- an execution task
            # that leaves a tree behind is the defect this issue exists to
            # remove, and it must not be reintroduced by the plumbing
            # (issue #125 Q9).
            logger.warning("job %s was cancelled; forcing its tree to stop", job_id)
            tree.stop_forced()
            raise

        # A job that exited in the same tick the stop arrived for finished on
        # its own terms; reporting it as stopped would be wrong.
        if stop_waiter.done() and not exit_waiter.done():
            stop_reason = "shutdown"
            tree_verified = await _stop_tree(tree, job_id, exit_waiter)
    finally:
        for waiter in (exit_waiter, stop_waiter):
            waiter.cancel()
        stop_readers.set()
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        stdout_writer.close()
        stderr_writer.close()
        # Closed on every path, not only the tidy one: the handle is a kernel
        # resource and an agent runs for days. On Windows it is also what
        # fires JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE for any rank the launcher
        # left behind, which is the intended reading of "the agent owns this
        # tree from launch to close" -- the ownership does not end when the
        # launcher exits (issue #125 §4 Q2).
        tree.close()

    exit_code = exit_waiter.result() if exit_waiter.done() else None

    # Determine terminal status and error code
    if stop_reason is not None:
        status = JobStatus.FAILED
        error_code = (
            _stopped(stop_reason) if tree_verified else JOB_ERRORS["tree_survived"]
        )
    elif exit_code == 0:
        status = JobStatus.COMPLETED
        error_code = None
    elif exit_code == 127 and sys.platform != "win32":
        # A child's 127 is the POSIX shell's "command not found", and it is the
        # *submitter's* typo: INFO, not ERROR, because the agent did exactly
        # what it was asked. cmd.exe and pwsh both return 1 for a missing
        # program and their message is localized (measured: Russian on the
        # PM's host), so there is nothing portable to match on -- a Windows
        # typo is reported honestly as process_failed:exit_code=1 with the
        # shell's own words in the stderr log (issue #125 Q5).
        logger.info("job %s: the shell reported no such command", job_id)
        status = JobStatus.FAILED
        error_code = JOB_ERRORS["command_not_found"]
    else:
        status = JobStatus.FAILED
        error_code = _process_failed(exit_code)

    # Write terminal state
    await write_terminal_state(
        paths=paths,
        job_id=job_id,
        status=status,
        exit_code=exit_code,
        error=error_code,
        watcher=watcher,
    )

    logger.info(
        "job %s completed with exit_code=%s status=%s", job_id, exit_code, status.value
    )

    return ExecutionResult(
        exit_code=exit_code,
        error_code=error_code,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )
