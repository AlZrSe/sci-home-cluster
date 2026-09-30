"""
Aggregated job metrics.
"""

from typing import List

from pydantic import BaseModel, Field

from .cpu_metric import CPUMetric
from .gpu_metric import GPUMetric


class JobMetricsSummary(BaseModel):
    """Summary statistics over a job's metric samples."""

    gpu_memory_min_mb: int = Field(..., ge=0)
    gpu_memory_max_mb: int = Field(..., ge=0)
    gpu_memory_avg_mb: int = Field(..., ge=0)
    gpu_util_min: int = Field(..., ge=0, le=100)
    gpu_util_max: int = Field(..., ge=0, le=100)
    gpu_util_avg: int = Field(..., ge=0, le=100)
    cpu_avg_percent: int = Field(..., ge=0, le=100)

    model_config = {"from_attributes": True}


class JobMetrics(BaseModel):
    """GPU and CPU metrics for a job."""

    job_id: str
    gpu_metrics: List[GPUMetric]
    cpu_metrics: List[CPUMetric]
    summary: JobMetricsSummary

    model_config = {"from_attributes": True}
