from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class GPUInfo(BaseModel):
    name: str
    memory_gb: int


class NodeSpec(BaseModel):
    node_id: str
    hostname: str
    gpus: List[GPUInfo]
    cpus: int = Field(..., ge=1)
    memory_gb: int = Field(..., ge=1)
    os: str
    status: str = Field(..., pattern='^(ONLINE|OFFLINE)$')
    last_heartbeat: datetime
    current_job_id: Optional[str] = None