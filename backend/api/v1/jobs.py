"""
Job management API endpoints.
"""

from typing import Any, Dict, Optional, Union
import asyncio
from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
    WebSocket,
)
from backend.core.deps import (
    get_current_token_payload,
    get_ws_token_payload,
    ws_accepted_subprotocol,
)
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
from shared.schemas.error_response import ErrorResponse
from backend.services.job_service import JobService

router = APIRouter()

# Sent as the WebSocket close reason when log streaming is unavailable
# because SEED_DEMO_DATA is off. Without it the client is left holding an
# open socket that will never emit anything and never close, which reads
# as a hung job (issue #35, AC-8).
LOG_STREAM_UNAVAILABLE_REASON = (
    "Log streaming is disabled: SEED_DEMO_DATA is off, so there are no "
    "log lines to stream. No agent writes job logs yet."
)

# Declared on the decorators so the generated OpenAPI documents them.
# These 404s are real and reachable: get_job_metrics returns None for an
# unknown job, and get_job_logs returns None for one too. That second one
# is what issue #35 fixed - LOGS_NOT_FOUND used to be dead code, because
# the store invented logs for any id at all.
METRICS_404: Dict[Union[int, str], Dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Job not found"}
}
LOGS_404: Dict[Union[int, str], Dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Job not found"}
}


@router.get("/", response_model=JobListResult)
async def list_jobs(
    job_status: Optional[str] = Query(None, alias="status"),
    node: Optional[str] = Query(None),
    node_id: Optional[str] = Query(
        None,
        description="Alias for `node`. The web client sends `node_id`; "
        "unknown query parameters are ignored by FastAPI, so without this "
        "alias node filtering silently returned unfiltered results.",
    ),
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
        status=job_status,
        node_id=node or node_id,
        search=search,
        limit=limit,
        offset=offset,
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


@router.get("/{job_id}/metrics", response_model=JobMetrics, responses=METRICS_404)
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


@router.get("/{job_id}/logs", responses=LOGS_404)
async def get_job_logs(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get job logs (HTTP fallback).
    """
    job_service = JobService()
    logs = await job_service.get_job_logs(job_id)
    if logs is None:
        raise logs_not_found(job_id)
    return logs


@router.get("/{job_id}/logs/history", responses=LOGS_404)
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
    # Echo the subprotocol the client offered, if any, so a browser client
    # that authenticates via Sec-WebSocket-Protocol completes the handshake.
    accepted_subprotocol = ws_accepted_subprotocol(websocket)
    await websocket.accept(subprotocol=accepted_subprotocol)
    job_service = JobService()

    # Check if job exists
    job = await job_service.get_job(job_id)
    if job is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Job not found"
        )
        return

    # Every streamed line is generated. With demo data off there is nothing
    # to send, so say so and close rather than hold the connection open and
    # silent (issue #35, AC-8).
    if not job_service.log_stream_available():
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=LOG_STREAM_UNAVAILABLE_REASON,
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

    stream_task = await job_service.start_log_stream(job_id, send_line)
    if stream_task is None:
        # Belt and braces: the route checked above, but the store is the
        # authority on whether it will stream, and it can be flipped
        # between the two checks.
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=LOG_STREAM_UNAVAILABLE_REASON,
        )
        job_service.unsubscribe_from_log_stream(job_id, send_line)
        return

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
