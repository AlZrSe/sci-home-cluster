from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    status: int = Field(..., ge=400, le=599)
    title: str
    detail: str
    instance: str