"""
Job management API endpoints.
"""

from typing import Optional
import asyncio
from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
    HTTPException,
    WebSocket,
)
from backend.core.deps import get_current_token_payload, get_ws_token_payload
from shared.schemas.job_state import JobState
from shared.schemas.job_list_result import JobListResult
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_metrics import JobMetrics
from backend.services.job_service import JobService

router = APIRouter()


@router.get("/", response_model=JobListResult)
async def list_jobs(
    job_status: Optional[str] = Query(None, alias="status"),
    node: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    payload: dict = Depends(get_current_token_payload),
):
    """
    List jobs with filtering and pagination.
    """
    job_service = JobService()
    jobs, total = await job_service.list_jobs(
        status=job_status, node_id=node, search=search, limit=limit, offset=offset
    )
    return JobListResult(items=jobs, total=total)


@router.post("/", response_model=JobState, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_spec: JobSpec,
    payload: dict = Depends(get_current_token_payload),
):
    """
    Create a new job from JSON spec.
    """
    job_service = JobService()
    created_job = await job_service.create_job(job_spec)
    return created_job


@router.get("/{job_id}", response_model=JobState)
async def get_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get a specific job by ID.
    """
    job_service = JobService()
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Job {job_id} does not exist. "
                "It may have been deleted or the ID is incorrect. "
                "Check the job list and try again."
            ),
        )
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Delete a job by ID.
    """
    job_service = JobService()
    deleted = await job_service.delete_job(job_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Job {job_id} does not exist. "
                "It may have been deleted or the ID is incorrect. "
                "Check the job list and try again."
            ),
        )
    return None


@router.get("/{job_id}/metrics", response_model=JobMetrics)
async def get_job_metrics(
    job_id: str, payload: dict = Depends(get_current_token_payload)
):
    """
    Get job GPU/CPU metrics.
    """
    job_service = JobService()
    metrics = await job_service.get_job_metrics(job_id)
    if metrics is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Metrics for job {job_id} are not available. "
                "The job may not have run yet or metrics collection failed. "
                "Ensure the job has started and try again."
            ),
        )
    return metrics


@router.get("/{job_id}/logs")
async def get_job_logs(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get job logs (HTTP fallback).
    """
    job_service = JobService()
    logs = await job_service.get_job_logs(job_id)
    if logs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Logs for job {job_id} are not available. "
                "The job may not have started yet or log collection failed. "
                "Ensure the job has started and try again."
            ),
        )
    return logs


@router.get("/{job_id}/logs/history")
async def get_job_logs_history(
    job_id: str, payload: dict = Depends(get_current_token_payload)
):
    """
    Get job logs history (alias for /logs).
    """
    return await get_job_logs(job_id, payload)


@router.websocket("/{job_id}/logs")
async def stream_job_logs(
    websocket: WebSocket,
    job_id: str,
    payload: dict = Depends(get_ws_token_payload),
):
    """
    Stream job logs via WebSocket.
    """
    await websocket.accept()
    job_service = JobService()

    # Check if job exists
    job = await job_service.get_job(job_id)
    if job is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Job not found"
        )
        return

    # Define a callback to send log lines to the WebSocket
    async def on_line(line: str):
        try:
            await websocket.send_text(line)
        except Exception:
            # If sending fails, we assume the client disconnected
            pass

    # Start the log streaming simulation
    _ = await job_service.start_log_stream(job_id, on_line)

    # Also subscribe to get updates (though start_log_stream already sets
    # up the streaming). We'll use the same callback for simplicity.
    job_service.subscribe_to_log_stream(
        job_id, lambda line: asyncio.create_task(on_line(line))
    )

    try:
        # Keep the connection open until the client disconnects
        while True:
            # Wait for any message from the client (we don't expect any,
            # but we need to keep the connection alive)
            await websocket.receive_text()
    except Exception:
        # Client disconnected or error occurred
        pass
    finally:
        # Clean up: stop the stream task and unsubscribe
        await job_service._stop_log_stream(job_id)
        job_service.unsubscribe_from_log_stream(job_id, on_line)


@router.post("/{job_id}/retry", response_model=JobState)
async def retry_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Retry a failed/cancelled job.
    """
    job_service = JobService()
    job = await job_service.retry_job(job_id)
    if job is None:
        # Check if job exists to give better error message
        existing_job = await job_service.get_job(job_id)
        if existing_job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Job {job_id} does not exist. "
                    "It may have been deleted or the ID is incorrect. "
                    "Check the job list and try again."
                ),
            )
        # Job exists but not in retryable state
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job {job_id} cannot be retried because it is currently "
                f"{existing_job.status.value}. "
                "Only FAILED or CANCELLED jobs can be retried."
            ),
        )
    return job


@router.post("/{job_id}/cancel", response_model=JobState)
async def cancel_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Cancel a running/pending job.
    """
    job_service = JobService()
    job = await job_service.cancel_job(job_id)
    if job is None:
        # Check if job exists to give better error message
        existing_job = await job_service.get_job(job_id)
        if existing_job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Job {job_id} does not exist. "
                    "It may have been deleted or the ID is incorrect. "
                    "Check the job list and try again."
                ),
            )
        # Job exists but not in cancellable state
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job {job_id} cannot be cancelled because it is currently "
                f"{existing_job.status.value}. "
                "Only RUNNING or PENDING jobs can be cancelled."
            ),
        )
    return job
