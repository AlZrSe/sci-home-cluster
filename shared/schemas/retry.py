"""
Retry policy for a job.
"""

from pydantic import BaseModel, Field


class RetryPolicy(BaseModel):
    """Retry configuration for a job."""

    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: int = Field(default=60, ge=0, le=3600)

    model_config = {"from_attributes": True}


# Backwards-compatible alias used by the original shared schema module.
JobRetry = RetryPolicy
