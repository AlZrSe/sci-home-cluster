"""
Job management API endpoints.
"""

from fastapi import APIRouter, Depends
from backend.core.deps import get_current_token_payload

router = APIRouter()


@router.get("/")
async def list_jobs(payload: dict = Depends(get_current_token_payload)):
    """
    List all jobs.
    """
    # TODO: Implement job listing
    return {"message": "List jobs endpoint - to be implemented"}


@router.post("/")
async def create_job(payload: dict = Depends(get_current_token_payload)):
    """
    Create a new job.
    """
    # TODO: Implement job creation
    return {"message": "Create job endpoint - to be implemented"}


@router.get("/{job_id}")
async def get_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get a specific job by ID.
    """
    # TODO: Implement get job
    return {"message": f"Get job {job_id} endpoint - to be implemented"}


@router.delete("/{job_id}")
async def delete_job(job_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Delete a job by ID.
    """
    # TODO: Implement job deletion
    return {"message": f"Delete job {job_id} endpoint - to be implemented"}