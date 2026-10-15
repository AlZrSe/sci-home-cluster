"""
Log entry model.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LogEntry(BaseModel):
    """A single log line with its origin marker."""

    timestamp: datetime
    line: str
    origin: Literal["synthetic", "real"] = Field(default="synthetic")

    model_config = {"from_attributes": True}
