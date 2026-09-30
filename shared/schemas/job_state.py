"""
Runtime state of a job in the cluster.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .job_spec import JobSpec
from .job_status import JobStatus


class JobState(BaseModel):
    """Runtime state of a job in the cluster."""

    job_id: str = Field(..., pattern="^job-\\d+$")
    spec: JobSpec
    status: JobStatus
    node_id: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    retry_count: int = Field(..., ge=0)

    model_config = {"from_attributes": True}
