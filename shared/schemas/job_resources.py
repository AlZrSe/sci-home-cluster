"""
Job resources model for the Scientific Home Cluster.
"""

from pydantic import BaseModel


class JobResources(BaseModel):
    """Computing resources required for a job."""

    gpus: int
    cpus: int
    memory_gb: int
    vram_gb: int

    model_config = {
        "from_attributes": True,
    }
