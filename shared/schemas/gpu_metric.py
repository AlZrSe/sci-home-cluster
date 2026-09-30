"""
GPU metric models.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class GPUInfo(BaseModel):
    """Information about a single GPU on a node."""

    name: str
    memory_gb: int

    model_config = {"from_attributes": True}


class GPUMetric(BaseModel):
    """GPU utilization and memory metrics."""

    timestamp: datetime
    gpu_index: int = Field(..., ge=0)
    memory_used_mb: int = Field(..., ge=0)
    memory_total_mb: int = Field(..., ge=0)
    utilization_percent: int = Field(..., ge=0, le=100)
    temperature_c: int = Field(..., ge=-50, le=150)

    model_config = {"from_attributes": True}
