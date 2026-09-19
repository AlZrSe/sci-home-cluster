from pydantic import BaseModel, Field
from typing import List
from .job_state import JobState


class JobListResult(BaseModel):
    items: List[JobState]
    total: int = Field(..., ge=0)