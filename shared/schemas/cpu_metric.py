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
    temperature_c: int = Field(..., ge=-50, le=150)
    memory_used_gb: float = Field(..., ge=0)

    model_config = {"from_attributes": True}
