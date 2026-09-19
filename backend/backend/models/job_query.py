from pydantic import BaseModel, Field
from typing import Optional
from .job_status import JobStatus


class JobQuery(BaseModel):
    status: Optional[JobStatus] = Field(default=None)  # Will handle ALL case in service layer
    node: Optional[str] = None
    search: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)