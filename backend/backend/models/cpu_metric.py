from pydantic import BaseModel, Field
from datetime import datetime


class CPUMetric(BaseModel):
    timestamp: datetime
    cpu_percent: int = Field(..., ge=0, le=100)
    memory_percent: int = Field(..., ge=0, le=100)