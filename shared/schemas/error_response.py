"""
Error response body returned by the API.
"""

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """RFC7807-style error body with a stable machine-readable code."""

    status: int = Field(..., ge=400, le=599)
    title: str
    detail: str
    instance: str
    error_code: str

    model_config = {"from_attributes": True}
