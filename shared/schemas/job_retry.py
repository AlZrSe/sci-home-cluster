"""
Job retry model for the Scientific Home Cluster.
"""

from pydantic import BaseModel


class JobRetry(BaseModel):
    """Retry configuration for a job."""

    max_retries: int
    retry_delay_seconds: int

    model_config = {
        "from_attributes": True,
    }
