"""
CPU metric model.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class CPUMetric(BaseModel):
    """CPU utilization and memory metrics."""

    timestamp: datetime
    cpu_percent: int = Field(..., ge=0, le=100)
    memory_percent: int = Field(..., ge=0, le=100)

    model_config = {"from_attributes": True}
