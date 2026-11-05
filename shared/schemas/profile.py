"""Profile schema for the Scientific Home Cluster."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class ProfileEnvEntry(BaseModel):
    key: str
    value: str

    model_config = {"from_attributes": True}


class ProfileSpec(BaseModel):
    id: str = Field(..., min_length=1)
    name: str
    software: str
    description: str = ""
    command: str = ""
    working_dir: str = ""
    gpus: int = 0
    cpus: int = 1
    memory_gb: int = 0
    vram_gb: int = 0
    input: str = ""
    output: str = ""
    max_retries: int = 0
    retry_delay_seconds: int = 0
    env: List[ProfileEnvEntry] = Field(default_factory=list)
    builtin: bool = False

    model_config = {"from_attributes": True}
