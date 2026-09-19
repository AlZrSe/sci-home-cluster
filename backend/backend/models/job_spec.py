from pydantic import BaseModel, Field
from typing import Dict, Optional


class Resources(BaseModel):
    gpus: int = Field(..., ge=0, le=8)
    cpus: int = Field(..., ge=1, le=128)
    memory_gb: int = Field(..., ge=1, le=1024)


class Paths(BaseModel):
    input: Optional[str] = None
    output: Optional[str] = None


class RetryPolicy(BaseModel):
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: int = Field(default=60, ge=0, le=3600)


class JobSpec(BaseModel):
    name: str = Field(..., pattern='^[a-zA-Z0-9_-]+$', min_length=3)
    command: str = Field(..., min_length=3)
    working_dir: str = Field(default="/sync/projects/new-run")
    env: Dict[str, str] = Field(default_factory=dict)
    resources: Resources
    paths: Paths = Field(default_factory=Paths)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)