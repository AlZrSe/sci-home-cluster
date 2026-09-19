from pydantic import BaseModel, Field
from datetime import datetime


class GPUMetric(BaseModel):
    timestamp: datetime
    gpu_index: int = Field(..., ge=0)
    memory_used_mb: int = Field(..., ge=0)
    memory_total_mb: int = Field(..., ge=0)
    utilization_percent: int = Field(..., ge=0, le=100)
    temperature_c: int = Field(..., ge=-50, le=150)  # Reasonable temperature range for GPUs