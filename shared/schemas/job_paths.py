"""
Job paths model for the Scientific Home Cluster.
"""

from pydantic import BaseModel


class JobPaths(BaseModel):
    """Input and output paths for a job."""

    input: str
    output: str

    model_config = {
        "from_attributes": True,
    }
