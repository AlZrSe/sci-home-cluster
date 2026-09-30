"""
Node specification.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from .gpu_metric import GPUInfo

__all__ = ["GPUInfo", "NodeSpec"]


class NodeSpec(BaseModel):
    """Specification for a compute node in the cluster."""

    node_id: str
    hostname: str
    gpus: List[GPUInfo]
    cpus: int = Field(..., ge=1)
    memory_gb: int = Field(..., ge=1)
    os: str
    status: str = Field(..., pattern="^(ONLINE|OFFLINE)$")
    last_heartbeat: datetime
    current_job_id: Optional[str] = None

    model_config = {"from_attributes": True}
