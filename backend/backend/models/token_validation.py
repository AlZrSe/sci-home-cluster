from pydantic import BaseModel, Field


class TokenValidationRequest(BaseModel):
    token: str = Field(..., min_length=8)


class TokenValidationResponse(BaseModel):
    valid: bool