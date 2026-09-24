"""
Job execution model for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from .job_spec import JobSpec
from .job_state import JobState


class JobExecution(BaseModel):
    """Record of a job execution in the cluster."""

    job_id: str
    spec: JobSpec
    state: JobState
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    exit_code: Optional[int] = None

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True,
        "use_enum_values": True,
        "validate_default": True,
    }
