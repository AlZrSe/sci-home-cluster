"""
Job metrics model for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from typing import List
from .gpu_metric import GPUMetric
from .cpu_metric import CPUMetric


class JobMetricsSummary(BaseModel):
    """Summary of job metrics."""

    gpu_memory_min_mb: int
    gpu_memory_max_mb: int
    gpu_memory_avg_mb: int
    gpu_util_min: int
    gpu_util_max: int
    gpu_util_avg: int
    cpu_avg_percent: float


class JobMetrics(BaseModel):
    """GPU and CPU metrics for a job."""

    job_id: str
    gpu_metrics: List[GPUMetric]
    cpu_metrics: List[CPUMetric]
    summary: JobMetricsSummary

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True,
        "use_enum_values": True,
        "validate_default": True,
    }
