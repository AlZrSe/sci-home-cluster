"""
Terminal state writing for the worker agent.

This module handles writing the final job state (COMPLETED/FAILED/CANCELLED)
to the state.yaml file when a job finishes execution.

``exit_code`` is ``int | None`` and not ``int``. The ``None`` is not a
sentinel: it means the job never had a process -- a stop that arrived before
the launch -- and there is no wait status to report. Forcing a number there
would mean inventing a code the backend has no meaning for, when ``-1``
already means "cancelled" and ``0xC000013A`` already means what Windows
returns for a process killed by ``CTRL_BREAK_EVENT`` (issue #125 Q5, D8).
"""

import logging
from datetime import datetime

from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus

from agent.paths import AgentPaths
from agent.watcher import FolderWatcher

logger = logging.getLogger(__name__)


async def write_terminal_state(
    *,
    paths: AgentPaths,
    job_id: str,
    status: JobStatus,
    exit_code: int | None,
    error: str | None,
    watcher: FolderWatcher | None = None,
) -> None:
    """Write COMPLETED/FAILED/CANCELLED to state.yaml. Never deletes the file.

    Reads the existing state to preserve spec, created_at, retry_count, and
    node_id, then updates with terminal fields and writes atomically.

    Args:
        paths: Resolved agent paths.
        job_id: The job identifier.
        status: Terminal status (COMPLETED, FAILED, or CANCELLED).
        exit_code: Process exit code, or ``None`` when no process ran.
        error: Machine-readable error code from the documented vocabulary,
               or None for successful completion.
        watcher: Optional FolderWatcher to record the write and suppress
                 re-observation by the agent's own watcher.
    """
    state_file = paths.jobs_dir / job_id / "state.yaml"
    now = datetime.now()  # naive local, matches store convention

    # Read current state to preserve spec, created_at, retry_count, node_id
    current = read_yaml(str(state_file), JobState)

    terminal_state = JobState(
        job_id=current.job_id,
        spec=current.spec,
        status=status,
        node_id=current.node_id,
        created_at=current.created_at,
        started_at=current.started_at,
        completed_at=now,
        exit_code=exit_code,
        error=error,
        retry_count=current.retry_count,  # NEVER incremented by agent
    )

    write_yaml(str(state_file), terminal_state)

    # Record in ledger so agent's own watcher ignores it
    if watcher is not None:
        watcher.ledger.record(state_file)

    logger.info(
        "wrote terminal state for %s: status=%s exit_code=%s error=%s",
        job_id,
        status.value,
        exit_code,
        error,
    )
