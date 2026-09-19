"""
Job management API endpoints.
"""

from typing import List, Optional
import asyncio
from fastapi import APIRouter, Depends, Query, status, UploadFile, File, HTTPException, WebSocket
from backend.core.deps import get_current_token_payload
from backend.models.job_state import JobState
from backend.models.job_query import JobQuery
from backend.models.job_list_result import JobListResult
from backend.models.job_spec import JobSpec
from backend.models.job_metrics import JobMetrics
import yaml
from backend.services.job_service import JobService

router = APIRouter()


@router.get("/", response_model=JobListResult)
async def list_jobs(
    job_status: Optional[str] = Query(None, alias="status"),
    node: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    payload: dict = Depends(get_current_token_payload)
):
    """
    List jobs with filtering and pagination.
    """
    job_service = JobService()
    jobs, total = await job_service.list_jobs(
        status=job_status,
        node_id=node,
        search=search,
        limit=limit,
        offset=offset
    )
    return JobListResult(items=jobs, total=total)


@router.post("/", response_model=JobState, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_yaml: UploadFile = File(..., alias="job.yaml"),
    payload: dict = Depends(get_current_token_payload)
):
    """
    Create a new job from YAML spec.
    """
    # TODO: Implement job creation logic
    try:
        # Read and parse the YAML file
        content = await job_yaml.read()
        job_data = yaml.safe_load(content)
        
        # Validate against JobSpec model
        job_spec = JobSpec(**job_data)
        
        # Create the job
        job_service = JobService()
        created_job = await job_service.create_job(job_spec)
        
        return created_job
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid job specification: {str(e)}"
        )


@router.get("/{job_id}", response_model=JobState)
async def get_job(
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Get a specific job by ID.
    """
    # TODO: Implement get job logic
    job_service = JobService()
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Delete a job by ID.
    """
    # TODO: Implement job deletion logic
    job_service = JobService()
    deleted = await job_service.delete_job(job_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    return None


@router.get("/{job_id}/metrics", response_model=JobMetrics)
async def get_job_metrics(
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Get job GPU/CPU metrics.
    """
    # TODO: Implement actual metrics retrieval logic
    job_service = JobService()
    metrics = await job_service.get_job_metrics(job_id)
    if metrics is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Metrics for job {job_id} not found"
        )
    return metrics


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Get job logs (HTTP fallback).
    """
    # TODO: Implement actual logs retrieval logic
    job_service = JobService()
    logs = await job_service.get_job_logs(job_id)
    if logs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Logs for job {job_id} not found"
        )
    return logs


@router.websocket("/{job_id}/logs/stream")
async def stream_job_logs(
    websocket: WebSocket,
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Stream job logs via WebSocket.
    """
    await websocket.accept()
    job_service = JobService()
    
    # Define a callback to send log lines to the WebSocket
    async def on_line(line: str):
        try:
            await websocket.send_text(line)
        except Exception:
            # If sending fails, we assume the client disconnected
            pass
    
    # Start the log streaming simulation
    stream_task = await job_service.start_log_stream(job_id, on_line)
    
    # Also subscribe to get updates (though start_log_stream already sets up the streaming)
    # We'll use the same callback for simplicity
    job_service.subscribe_to_log_stream(job_id, lambda line: asyncio.create_task(on_line(line)))
    
    try:
        # Keep the connection open until the client disconnects
        while True:
            # Wait for any message from the client (we don't expect any, but we need to keep the connection alive)
            # We'll just wait for a disconnect
            await websocket.receive_text()
    except Exception:
        # Client disconnected or error occurred
        pass
    finally:
        # Clean up: stop the stream task and unsubscribe
        job_service._stop_log_stream(job_id)
        job_service.unsubscribe_from_log_stream(job_id, on_line)


@router.post("/{job_id}/retry", response_model=JobState)
async def retry_job(
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Retry a failed/cancelled job.
    """
    # TODO: Implement job retry logic
    job_service = JobService()
    job = await job_service.retry_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} not in retryable state"
        )
    return job


@router.post("/{job_id}/cancel", response_model=JobState)
async def cancel_job(
    job_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Cancel a running/pending job.
    """
    # TODO: Implement job cancellation logic
    job_service = JobService()
    job = await job_service.cancel_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} not cancellable"
        )
    return job