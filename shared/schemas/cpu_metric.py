"""
CPU metric model for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from datetime import datetime


class CPUMetric(BaseModel):
    """CPU utilization and memory metrics."""

    timestamp: datetime
    cpu_percent: float
    memory_percent: float

    model_config = {
        "from_attributes": True,
    }
