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
    WebSocket,
)
from backend.core.deps import get_current_token_payload, get_ws_token_payload
from backend.core.errors import (
    job_not_cancellable,
    job_not_found,
    job_not_retryable,
    logs_not_found,
    metrics_not_found,
)
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
        raise job_not_found(job_id)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Delete a job by ID.
    """
    job_service = JobService()
    deleted = await job_service.delete_job(job_id)
    if not deleted:
        raise job_not_found(job_id)
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
        raise metrics_not_found(job_id)
    return metrics


@router.get("/{job_id}/logs")
async def get_job_logs(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get job logs (HTTP fallback).
    """
    job_service = JobService()
    logs = await job_service.get_job_logs(job_id)
    if logs is None:
        raise logs_not_found(job_id)
    return logs


@router.get("/{job_id}/logs/history")
async def get_job_logs_history(
    job_id: str, payload: dict = Depends(get_current_token_payload)
):
    """
    Get job logs history (alias for /logs).
    """
    job_service = JobService()
    logs = await job_service.get_job_logs(job_id)
    if logs is None:
        raise logs_not_found(job_id)
    return logs


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

    # start_log_stream registers on_line as the sole subscriber. Subscribing
    # again here (as this route used to) delivered every line twice and left
    # an orphaned subscription, because the lambda that was registered could
    # never be matched by the unsubscribe call in the finally block.
    def send_line(line: str) -> None:
        asyncio.create_task(on_line(line))

    await job_service.start_log_stream(job_id, send_line)

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
        # Clean up: stop the stream task and unsubscribe the same callback
        await job_service.stop_log_stream(job_id)
        job_service.unsubscribe_from_log_stream(job_id, send_line)


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
            raise job_not_found(job_id)
        # Job exists but not in retryable state
        raise job_not_retryable(job_id, existing_job.status.value)
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
            raise job_not_found(job_id)
        # Job exists but not in cancellable state
        raise job_not_cancellable(job_id, existing_job.status.value)
    return job
