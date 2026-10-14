"""
Agent claim protocol for job discovery and assignment.

This module implements the claim logic for the worker agent to discover PENDING
jobs, filter by resource compatibility, and atomically claim them using an
advisory marker file.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec

from agent.paths import AgentPaths


def can_fit_job(node_spec: NodeSpec, job_spec: JobSpec) -> Tuple[bool, str]:
    """
    Check if a node can satisfy a job's resource requirements.

    Args:
        node_spec: The node's hardware specification.
        job_spec: The job's resource requirements.

    Returns:
        A tuple of (fits, reason). If fits is True, reason is empty.
        If fits is False, reason explains which resource is insufficient.
    """
    resources = job_spec.resources

    # Check GPU count
    gpu_count = len(node_spec.gpus)
    if resources.gpus > gpu_count:
        return False, f"gpus (need {resources.gpus}, have {gpu_count})"

    # Check CPUs
    if resources.cpus > node_spec.cpus:
        return False, f"cpus (need {resources.cpus}, have {node_spec.cpus})"

    # Check memory
    have_mem = node_spec.memory_gb
    if resources.memory_gb > have_mem:
        return False, f"memory_gb (need {resources.memory_gb}, have {have_mem})"

    # Check VRAM
    if resources.vram_gb > 0:
        if gpu_count == 0:
            return False, f"vram_gb (need {resources.vram_gb}, have 0: no GPUs)"
        max_vram = max(gpu.memory_gb for gpu in node_spec.gpus)
        if resources.vram_gb > max_vram:
            return False, f"vram_gb (need {resources.vram_gb}, max GPU has {max_vram})"

    return True, ""


def _is_job_state_file(path: Path) -> bool:
    """Check if a path is a jobs/<job_id>/state.yaml file."""
    return (
        path.name == "state.yaml"
        and path.parent.name.startswith("job-")
        and path.parent.parent.name == "jobs"
    )


def _get_job_id_from_state_path(path: Path) -> str:
    """Extract job_id from a jobs/<job_id>/state.yaml path."""
    return path.parent.name


def _read_job_state(state_file: Path) -> Tuple[JobState | None, str]:
    """Read job state from file. Returns (state, error_message)."""
    try:
        return read_yaml(str(state_file), JobState), ""
    except FileNotFoundError:
        return None, "state.yaml not found"
    except Exception as e:
        return None, f"failed to read state.yaml: {e}"


def _check_claimable(job_state: JobState) -> str | None:
    """Check if job is claimable. Returns error message or None if claimable."""
    if job_state.status != JobStatus.PENDING:
        return f"job status is {job_state.status.value}, not PENDING"
    if job_state.node_id is not None:
        return f"job already owned by {job_state.node_id}"
    return None


def _write_failed_state(
    state_file: Path, job_state: JobState, error_msg: str, now: datetime
) -> str | None:
    """Write FAILED state. Returns error message or None on success."""
    try:
        updated_state = JobState(
            job_id=job_state.job_id,
            spec=job_state.spec,
            status=JobStatus.FAILED,
            node_id=None,
            created_at=job_state.created_at,
            started_at=None,
            completed_at=now,
            exit_code=1,
            error=error_msg,
            retry_count=job_state.retry_count,
        )
        write_yaml(str(state_file), updated_state)
    except Exception as e:
        return f"failed to write FAILED state: {e}"
    return None


def _create_marker(marker_file: Path) -> str | None:
    """Create advisory marker file with O_EXCL.

    Returns error message or None on success.
    """
    try:
        fd = os.open(str(marker_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return "marker exists (another task claimed this job)"
    except Exception as e:
        return f"failed to create marker: {e}"
    return None


def _write_running_state(
    state_file: Path,
    job_state: JobState,
    node_id: str,
    now: datetime,
    marker_file: Path,
) -> str | None:
    """Write RUNNING state. Returns error message or None on success."""
    try:
        updated_state = JobState(
            job_id=job_state.job_id,
            spec=job_state.spec,
            status=JobStatus.RUNNING,
            node_id=node_id,
            created_at=job_state.created_at,
            started_at=now,
            completed_at=None,
            exit_code=None,
            error=None,
            retry_count=job_state.retry_count,
        )
        write_yaml(str(state_file), updated_state)
    except Exception as e:
        # Clean up marker on failure
        try:
            marker_file.unlink(missing_ok=True)
        except Exception:
            pass
        return f"failed to write RUNNING state: {e}"
    return None


async def try_claim_job(
    paths: AgentPaths,
    job_id: str,
    node_id: str,
    node_spec: NodeSpec,
    *,
    now: datetime | None = None,
) -> Tuple[bool, str]:
    """
    Attempt to claim a PENDING job for this node.

    Creates an advisory marker file jobs/<job_id>/.claim.<node_id> with O_EXCL
    to prevent concurrent claims on the same node. On success, updates
    state.yaml with status=RUNNING, node_id, and started_at.

    Args:
        paths: Resolved agent paths.
        job_id: The job ID to claim (from state.yaml).
        node_id: This node's ID.
        node_spec: This node's specification for resource check.
        now: Optional fixed timestamp for testing (defaults to datetime.now()).

    Returns:
        A tuple of (success, reason). On success, reason is empty.
        On failure, reason explains why the claim was not made.
    """
    if now is None:
        now = datetime.now()

    job_dir = paths.jobs_dir / job_id
    state_file = job_dir / "state.yaml"
    marker_file = job_dir / f".claim.{node_id}"

    # Read current state
    job_state, error = _read_job_state(state_file)
    if error:
        return False, error

    # At this point job_state is guaranteed to be not None
    assert job_state is not None

    # Check if job is claimable
    claimable_error = _check_claimable(job_state)
    if claimable_error:
        return False, claimable_error

    # Check resource compatibility
    fits, reason = can_fit_job(node_spec, job_state.spec)
    if not fits:
        error_msg = f"No node can satisfy job resources: {reason}"
        write_error = _write_failed_state(state_file, job_state, error_msg, now)
        if write_error:
            return False, write_error
        return False, error_msg

    # Try to create advisory marker file with O_EXCL
    marker_error = _create_marker(marker_file)
    if marker_error:
        return False, marker_error

    # Marker created successfully, now update state.yaml
    write_error = _write_running_state(state_file, job_state, node_id, now, marker_file)
    if write_error:
        return False, write_error

    return True, ""


async def claim_job(
    paths: AgentPaths,
    state_file_path: Path,
    node_id: str,
    node_spec: NodeSpec,
    *,
    now: datetime | None = None,
) -> Tuple[bool, str]:
    """
    High-level claim function that processes a state.yaml file event.

    This is the main entry point for the agent loop to claim a job from a
    FolderEvent pointing to a jobs/<job_id>/state.yaml file.

    Args:
        paths: Resolved agent paths.
        state_file_path: Path to the state.yaml file (from FolderEvent).
        node_id: This node's ID.
        node_spec: This node's specification for resource check.
        now: Optional fixed timestamp for testing.

    Returns:
        A tuple of (success, reason). On success, reason is empty.
    """
    if not _is_job_state_file(state_file_path):
        return False, "not a job state file"

    job_id = _get_job_id_from_state_path(state_file_path)
    return await try_claim_job(paths, job_id, node_id, node_spec, now=now)
