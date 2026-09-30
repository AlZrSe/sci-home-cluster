"""
Computing resources required by a job.
"""

from pydantic import BaseModel, Field


class Resources(BaseModel):
    """Computing resources required by a job."""

    gpus: int = Field(..., ge=0, le=8)
    cpus: int = Field(..., ge=1, le=128)
    memory_gb: int = Field(..., ge=1, le=1024)
    vram_gb: int = Field(default=0, ge=0, le=80)

    model_config = {"from_attributes": True}


# Backwards-compatible alias used by the original shared schema module.
JobResources = Resources
