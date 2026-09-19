from pydantic import BaseModel, Field
from typing import List
from .gpu_metric import GPUMetric
from .cpu_metric import CPUMetric


class JobMetricsSummary(BaseModel):
    gpu_memory_min_mb: int = Field(..., ge=0)
    gpu_memory_max_mb: int = Field(..., ge=0)
    gpu_memory_avg_mb: int = Field(..., ge=0)
    gpu_util_min: int = Field(..., ge=0, le=100)
    gpu_util_max: int = Field(..., ge=0, le=100)
    gpu_util_avg: int = Field(..., ge=0, le=100)
    cpu_avg_percent: int = Field(..., ge=0, le=100)


class JobMetrics(BaseModel):
    job_id: str
    gpu_metrics: List[GPUMetric]
    cpu_metrics: List[CPUMetric]
    summary: JobMetricsSummary