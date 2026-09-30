"""
Paginated job listing result.
"""

from typing import List

from pydantic import BaseModel, Field

from .job_state import JobState


class JobListResult(BaseModel):
    """A page of jobs plus the total number matching the query."""

    items: List[JobState]
    total: int = Field(..., ge=0)

    model_config = {"from_attributes": True}
