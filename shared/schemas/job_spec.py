"""
Job specification, the contract between a submitter and a worker node.
"""

from typing import Dict

from pydantic import BaseModel, Field

from .paths import Paths
from .resources import Resources


class JobSpec(BaseModel):
    """Specification for a job to be executed in the cluster."""

    name: str = Field(..., pattern="^[a-zA-Z0-9_-]+$", min_length=3)
    command: str = Field(..., min_length=3)
    working_dir: str = Field(default="/sync/projects/new-run")
    env: Dict[str, str] = Field(default_factory=dict)
    resources: Resources
    paths: Paths = Field(default_factory=Paths)

    model_config = {"from_attributes": True}
