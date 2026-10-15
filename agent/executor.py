"""
Job execution engine for the worker agent.

This module implements the core logic for launching and managing job processes,
including environment preparation, resource enforcement, log capture, and
terminal state reporting.
"""

import asyncio
import logging
import os
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from shared.file_ops.path_utils import is_within_syncthing_root, normalize_path
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec

from agent.log_writer import BoundedLineWriter
from agent.paths import AgentPaths
from agent.terminal import write_terminal_state
from agent.watcher import FolderWatcher

logger = logging.getLogger(__name__)


class ExecutionResult:
    """Result of a job execution."""

    def __init__(
        self,
        exit_code: int,
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


def validate_and_prepare_working_dir(
    working_dir: str,
    syncthing_root: Path,
) -> tuple[Path, Optional[str]]:
    """
    Validate and prepare the working directory.

    Returns:
        (resolved_path, error_message). Error message is None on success.
    """
    resolved = normalize_path(Path(working_dir))

    if not is_within_syncthing_root(resolved):
        # Outside Syncthing root: must exist, we don't create it
        if not resolved.exists():
            return resolved, "launch_failed:working_dir_not_found"
        return resolved, None

    # Inside Syncthing root: create parents if needed
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return resolved, f"launch_failed:working_dir_create_failed: {e}"

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


async def execute_job(  # noqa: C901 - complex execution logic with many validation paths
    *,
    job_id: str,
    job_spec: JobSpec,
    node_spec: NodeSpec,
    paths: AgentPaths,
    syncthing_root: Path,
    stop_event: asyncio.Event,
    watcher: FolderWatcher | None = None,
) -> ExecutionResult:
    """
    Execute a job and return ExecutionResult.

    The function:
    - Validates working_dir and paths.input
    - Builds the child environment
    - Sets up resource enforcement (GPU, CPU affinity)
    - Launches the process via /bin/sh -c in a new session
    - Captures stdout/stderr to log files under jobs/<job_id>/logs/
    - Returns when the process exits or stop_event is set
    """
    # Validate working directory
    working_dir, error = validate_and_prepare_working_dir(
        job_spec.working_dir, syncthing_root
    )
    if error:
        logger.error("working_dir validation failed for %s: %s", job_id, error)
        await write_terminal_state(
            paths=paths,
            job_id=job_id,
            status=JobStatus.FAILED,
            exit_code=1,
            error=error,
            watcher=watcher,
        )
        return ExecutionResult(
            exit_code=1,
            error_code=error,
            stdout_path="",
            stderr_path="",
        )

    # Validate paths.input if specified
    if job_spec.paths.input:
        input_path = normalize_path(Path(job_spec.paths.input))
        # Check if input is within Syncthing root
        if not is_within_syncthing_root(input_path):
            # Outside Syncthing root: must exist
            if not input_path.exists():
                error = "launch_failed:input_missing"
                logger.error("input path missing for %s: %s", job_id, input_path)
                await write_terminal_state(
                    paths=paths,
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    exit_code=1,
                    error=error,
                    watcher=watcher,
                )
                return ExecutionResult(
                    exit_code=1,
                    error_code=error,
                    stdout_path="",
                    stderr_path="",
                )
        else:
            # Inside Syncthing root: check existence
            if not input_path.exists():
                error = "launch_failed:input_missing"
                logger.error("input path missing for %s: %s", job_id, input_path)
                await write_terminal_state(
                    paths=paths,
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    exit_code=1,
                    error=error,
                    watcher=watcher,
                )
                return ExecutionResult(
                    exit_code=1,
                    error_code=error,
                    stdout_path="",
                    stderr_path="",
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
            error = "launch_failed:insufficient_gpus"
            logger.error(
                "GPU allocation failed for %s: need %d, have %d",
                job_id,
                job_spec.resources.gpus,
                available_gpus,
            )
            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.FAILED,
                exit_code=1,
                error=error,
                watcher=watcher,
            )
            return ExecutionResult(
                exit_code=1,
                error_code=error,
                stdout_path="",
                stderr_path="",
            )

    child_env = build_child_env(
        parent_env=dict(os.environ),
        spec_env=job_spec.env,
        gpu_indices=gpu_indices,
    )

    # Set up CPU affinity if requested (advisory only)
    cpu_affinity_set = None
    if job_spec.resources.cpus > 0:
        try:
            # Use first N CPUs
            cpu_set = set(range(job_spec.resources.cpus))
            cpu_affinity_set = cpu_set
        except Exception:
            pass  # Advisory only, will log if attempted

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

    try:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=working_dir,
            env=child_env,
            start_new_session=True,  # New process group
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=False,  # Binary mode for undecodable handling
        )
    except FileNotFoundError:
        error = "launch_failed:command_not_found"
        logger.error("shell not found for job %s", job_id)
        await write_terminal_state(
            paths=paths,
            job_id=job_id,
            status=JobStatus.FAILED,
            exit_code=127,
            error=error,
            watcher=watcher,
        )
        return ExecutionResult(
            exit_code=127,
            error_code=error,
            stdout_path="",
            stderr_path="",
        )
    except Exception as e:
        error = f"launch_failed:process_creation_failed: {e}"
        logger.error("process creation failed for %s: %s", job_id, e)
        await write_terminal_state(
            paths=paths,
            job_id=job_id,
            status=JobStatus.FAILED,
            exit_code=1,
            error=error,
            watcher=watcher,
        )
        return ExecutionResult(
            exit_code=1,
            error_code=error,
            stdout_path="",
            stderr_path="",
        )

    # Set CPU affinity in the child process (advisory)
    if cpu_affinity_set is not None:
        try:
            os.sched_setaffinity(proc.pid, cpu_affinity_set)
            logger.info(
                "set CPU affinity for job %s pid %%d: %%s",
                job_id,
                proc.pid,
                cpu_affinity_set,
            )
        except (AttributeError, OSError) as e:
            # Advisory only - log at debug level
            logger.debug("CPU affinity not set for job %s: %s", job_id, e)

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

    # Wait for process completion or stop event
    exit_code = None
    error_code = None

    try:
        while exit_code is None:
            # Check stop event
            if stop_event.is_set():
                logger.info("stop event received, terminating job %s", job_id)
                # Send SIGTERM to process group
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # Process already gone
                # Wait briefly then SIGKILL
                try:
                    await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5.0)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await asyncio.to_thread(proc.wait)

            # Check if process has exited
            exit_code = proc.poll()
            if exit_code is not None:
                break

            await asyncio.sleep(0.1)
    finally:
        stop_readers.set()
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        stdout_writer.close()
        stderr_writer.close()

    # Determine terminal status and error code
    if exit_code == 0:
        status = JobStatus.COMPLETED
        error_code = None
    elif exit_code == 127:
        status = JobStatus.FAILED
        error_code = "launch_failed:command_not_found"
    else:
        status = JobStatus.FAILED
        error_code = f"process_failed:exit_code={exit_code}"

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
        "job %s completed with exit_code=%d status=%s", job_id, exit_code, status.value
    )

    return ExecutionResult(
        exit_code=exit_code,
        error_code=error_code,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )
