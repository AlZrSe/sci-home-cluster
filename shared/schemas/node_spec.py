"""
Node specification model for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from .gpu_metric import GPUInfo


class NodeSpec(BaseModel):
    """Specification for a compute node in the cluster."""

    node_id: str
    hostname: str
    gpus: List[GPUInfo]
    cpus: int
    memory_gb: int
    os: str
    status: str  # e.g., ONLINE, OFFLINE, MAINTENANCE
    last_heartbeat: datetime
    current_job_id: Optional[str] = None

    model_config = {
        "from_attributes": True,
        # Allow using ORM mode if needed with SQLAlchemy
        "orm_mode": True,
        # Allow arbitrary types if needed
        "arbitrary_types_allowed": True,
    }
