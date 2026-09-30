"""
Filter and pagination parameters for job listings.
"""

from typing import Optional

from pydantic import BaseModel, Field

from .job_status import JobStatus


class JobQuery(BaseModel):
    """Query parameters accepted by GET /jobs.

    The router currently declares these as FastAPI Query() parameters; this
    model is the typed form of the same contract and is used by clients.
    """

    status: Optional[JobStatus] = None
    node: Optional[str] = None
    search: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    model_config = {"from_attributes": True}
