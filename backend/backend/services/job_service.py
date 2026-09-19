"""
Job service layer containing business logic for job management.
"""

from typing import List, Optional, Tuple
from datetime import datetime
from backend.core.config import settings
from backend.models.job_state import JobState
from backend.models.job_spec import JobSpec
from backend.models.job_status import JobStatus
from backend.models.job_metrics import JobMetrics
from backend.store.memory import get_store


class JobService:
    def __init__(self):
        # Initialize the store
        self._store = get_store()
    
    async def list_jobs(
        self, 
        status: Optional[str] = None,
        node_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Tuple[List[JobState], int]:
        """List jobs with filtering and pagination."""
        return await self._store.list_jobs(
            status=status,
            node_id=node_id,
            search=search,
            limit=limit,
            offset=offset
        )
    
    async def create_job(self, job_data: JobSpec) -> JobState:
        """Create a new job."""
        return await self._store.create_job(job_data)
    
    async def get_job(self, job_id: str) -> Optional[JobState]:
        """Get a job by ID."""
        return await self._store.get_job(job_id)
    
    async def delete_job(self, job_id: str) -> bool:
        """Delete a job by ID."""
        return await self._store.delete_job(job_id)
    
    async def get_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """Get job GPU/CPU metrics."""
        return await self._store.get_job_metrics(job_id)
    
    async def get_job_logs(self, job_id: str) -> Optional[List[str]]:
        """Get job logs."""
        return await self._store.get_job_logs(job_id)
    
    async def retry_job(self, job_id: str) -> Optional[JobState]:
        """Retry a failed/cancelled job."""
        job = await self._store.get_job(job_id)
        if not job:
            return None
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            return None
        # Reset the job to PENDING
        updated_job = await self._store.update_job(
            job_id,
            status=JobStatus.PENDING,
            node_id=None,  # When retried, it's not assigned to a node yet
            started_at=None,
            completed_at=None,
            exit_code=None,
            error=None,
            retry_count=job.retry_count + 1,
        )
        return updated_job
    
    async def cancel_job(self, job_id: str) -> Optional[JobState]:
        """Cancel a running/pending job."""
        job = await self._store.get_job(job_id)
        if not job:
            return None
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return None
        # Cancel the job
        now = datetime.now()
        updated_job = await self._store.update_job(
            job_id,
            status=JobStatus.CANCELLED,
            completed_at=now,
            exit_code=-1,  # Indicates cancelled
        )
        return updated_job