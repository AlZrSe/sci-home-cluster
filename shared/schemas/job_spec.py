"""
Job specification model for the Scientific Home Cluster.
"""

from pydantic import BaseModel
from typing import Dict
from .job_resources import JobResources
from .job_paths import JobPaths
from .job_retry import JobRetry


class JobSpec(BaseModel):
    """Specification for a job to be executed in the cluster."""

    name: str
    command: str
    working_dir: str
    env: Dict[str, str] = {}
    resources: JobResources
    paths: JobPaths
    retry: JobRetry

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True,
        "use_enum_values": True,
        "validate_default": True,
    }
