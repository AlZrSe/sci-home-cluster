"""
CPU metric model.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CPUMetric(BaseModel):
    """CPU utilization and memory metrics."""

    timestamp: datetime
    cpu_percent: int = Field(..., ge=0, le=100)
    memory_percent: int = Field(..., ge=0, le=100)
    temperature_c: int = Field(..., ge=-50, le=150)
    memory_used_gb: float = Field(..., ge=0)
    origin: Literal["synthetic", "real"] = Field(default="synthetic")

    model_config = {"from_attributes": True}
