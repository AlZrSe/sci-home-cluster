"""Metric segment schema for file-based metrics ingest (issue #102)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from shared.schemas.cpu_metric import CPUMetric
from shared.schemas.gpu_metric import GPUMetric

SEGMENT_ID_PATTERN = r"^metrics-\d{10,}-[0-9a-f]{6}$"


class MetricSegment(BaseModel):
    """One create-only batch of metric samples, written by the agent."""

    segment_id: str = Field(..., pattern=SEGMENT_ID_PATTERN)
    node_id: str = Field(..., min_length=1)
    job_id: Optional[str] = None
    started_at: datetime
    ended_at: datetime
    gpu_metrics: List[GPUMetric] = Field(default_factory=list)
    cpu_metrics: List[CPUMetric] = Field(default_factory=list)
    origin: str = Field(default="real")

    model_config = {"from_attributes": True}
