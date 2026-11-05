"""Log segment schema for file-based log ingest (issue #101)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, Field

from shared.schemas.log_entry import LogEntry


class LogSegment(BaseModel):
    """One create-only batch of log lines, written by the agent."""

    job_id: str = Field(..., min_length=1)
    stream: Literal["stdout", "stderr"]
    sequence: int = Field(..., ge=1)
    lines: List[LogEntry] = Field(default_factory=list)
    dropped: int = Field(default=0, ge=0)
    created_at: datetime

    model_config = {"from_attributes": True}
