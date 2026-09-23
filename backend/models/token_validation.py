from pydantic import BaseModel, Field


class TokenValidationRequest(BaseModel):
    token: str = Field(..., min_length=8)


class TokenValidationResponse(BaseModel):
    valid: bool


class TokenCreateRequest(BaseModel):
    """Request to create a new token (validates shared token)."""
    shared_token: str = Field(..., min_length=8, description="Shared token for authentication")


class TokenCreateResponse(BaseModel):
    """Response with newly created JWT token."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenRefreshRequest(BaseModel):
    """Request to refresh an existing token."""
    token: str = Field(..., min_length=8, description="Current valid token")


class TokenRefreshResponse(BaseModel):
    """Response with refreshed JWT token."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
