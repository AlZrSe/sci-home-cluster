"""
Job state model for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from .job_spec import JobSpec
from .job_status import JobStatus


class JobState(BaseModel):
    """Runtime state of a job in the cluster."""

    job_id: str
    spec: JobSpec
    status: JobStatus
    node_id: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    retry_count: int = 0

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True,
        "use_enum_values": True,
        "validate_default": True,
    }
