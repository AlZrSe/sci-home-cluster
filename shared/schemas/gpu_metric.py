"""
GPU metric models for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from datetime import datetime


class GPUInfo(BaseModel):
    """Information about a GPU."""

    name: str
    memory_gb: int

    model_config = {
        "from_attributes": True,
    }


class GPUMetric(BaseModel):
    """GPU utilization and memory metrics."""

    timestamp: datetime
    gpu_index: int
    memory_used_mb: int
    memory_total_mb: int
    utilization_percent: int
    temperature_c: int

    model_config = {
        "from_attributes": True,
    }
