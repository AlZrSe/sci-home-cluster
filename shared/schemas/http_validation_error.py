"""
FastAPI HTTP validation error schema.
"""

from pydantic import BaseModel, Field


class ValidationError(BaseModel):
    """Single validation error."""

    loc: list[str | int]
    msg: str
    type: str


class HTTPValidationError(BaseModel):
    """HTTP validation error response (FastAPI standard)."""

    detail: list[ValidationError] = Field(default_factory=list)

    model_config = {"from_attributes": True}
