"""
Operator command handling for the file-only agent (issue #106).

Pure decision logic (``classify_command``) plus narrow I/O helpers, so the
decision is testable with fakes and the module stays free of network imports
(``agent/tests/test_no_cluster_required.py``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.job_command import JobCommand
from shared.schemas.job_command_ack import JobCommandAck, JobCommandResult
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus

logger = logging.getLogger(__name__)

# The agent never deletes a command file (issue #106 D5): they are
# backend-owned and append-only. An agent deleting one would destroy the audit
# trail and race the backend's own watcher.


def classify_command(
    *,
    command: JobCommand,
    job_state: JobState | None,
    node_id: str,
    has_live_process: bool,
    already_acked: bool,
) -> JobCommandResult:
    """Decide what to do with one command. Pure: no I/O, no signals."""
    if already_acked:
        return JobCommandResult.ALREADY_TERMINAL
    if job_state is None:
        return JobCommandResult.IGNORED_NO_JOB
    if job_state.node_id is not None and job_state.node_id != node_id:
        return JobCommandResult.IGNORED_NOT_OWNER
    if job_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        return JobCommandResult.ALREADY_TERMINAL
    if not has_live_process:
        return JobCommandResult.ALREADY_TERMINAL
    return JobCommandResult.TERMINATED


def load_command(path: Path) -> JobCommand:
    """Read and validate a command file."""
    return read_yaml(str(path), JobCommand)


def load_ack(path: Path) -> Optional[JobCommandAck]:
    """Read an ack file, or None when absent or unreadable."""
    try:
        return read_yaml(str(path), JobCommandAck)
    except Exception:
        return None


async def handle_command(  # noqa: C901
    path: Path,
    *,
    paths: Any,
    node_id: str,
    current: Any = None,
    watcher: Any = None,
    job_state: Any = None,
    has_live_process: bool = False,
    already_acked: bool = False,
    job_stop: Any = None,
    set_reason: Any = None,
) -> JobCommandAck:
    """The impure half: classify, act if warranted, write the ack exactly once."""
    path = Path(path)
    command = load_command(path)
    job_id = path.parent.parent.name

    # Derive already_acked from disk if not provided.
    if hasattr(paths, "job_ack_file"):
        ack_path = paths.job_ack_file(job_id, command.command_id)
    else:
        ack_path = None
    if ack_path is not None and ack_path.exists() and not already_acked:
        already_acked = True

    # Resolve job_state if not provided.
    if job_state is None:
        if hasattr(paths, "jobs_dir"):
            state_file = paths.jobs_dir / job_id / "state.yaml"
        else:
            state_file = None
        if state_file is not None and state_file.exists():
            try:
                job_state = read_yaml(str(state_file), JobState)
            except Exception:
                job_state = None

    # Derive has_live_process if not provided.
    if not has_live_process and current == job_id and job_stop is not None:
        has_live_process = True

    result = classify_command(
        command=command,
        job_state=job_state,
        node_id=node_id,
        has_live_process=has_live_process,
        already_acked=already_acked,
    )
    logger.info(
        "observed %s command %s for job %s -> %s",
        command.command.value,
        command.command_id,
        job_id,
        result.value,
    )

    # Act if warranted.
    if result == JobCommandResult.TERMINATED:
        if job_stop is not None:
            try:
                job_stop.set()
            except Exception:
                pass
        if set_reason is not None:
            try:
                set_reason("cancel")
            except Exception:
                pass

    # Write the ack exactly once (skip if already present).
    if ack_path is not None and not ack_path.exists():
        try:
            if hasattr(paths, "job_acks_dir"):
                paths.job_acks_dir(job_id).mkdir(parents=True, exist_ok=True)
            ack = JobCommandAck(
                command_id=command.command_id,
                command=command.command,
                result=result,
                acknowledged_at=datetime.now(),
                acknowledged_by=node_id,
                detail=None,
            )
            if ack_path is not None:
                write_yaml(str(ack_path), ack)
                if watcher is not None and hasattr(watcher, "ledger"):
                    try:
                        watcher.ledger.record(ack_path)
                    except Exception:
                        pass
            return ack
        except OSError:
            logger.error("could not write ack for %s", command.command_id)
            # fall through: return an ack object anyway
    # Already acked: load existing.
    if ack_path is not None and ack_path.exists():
        existing = load_ack(ack_path)
    else:
        existing = None
    if existing is not None:
        return existing
    # Fallback ack (no disk write).
    return JobCommandAck(
        command_id=command.command_id,
        command=command.command,
        result=result,
        acknowledged_at=datetime.now(),
        acknowledged_by=node_id,
        detail=None,
    )
