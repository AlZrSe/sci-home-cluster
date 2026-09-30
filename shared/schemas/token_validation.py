"""
Token request and response bodies for the auth endpoints.
"""

from pydantic import BaseModel, Field


class TokenValidationRequest(BaseModel):
    """Request body carrying a token to validate."""

    token: str = Field(..., min_length=8)

    model_config = {"from_attributes": True}


class TokenValidationResponse(BaseModel):
    """Whether a token is currently valid."""

    valid: bool

    model_config = {"from_attributes": True}


class TokenCreateRequest(BaseModel):
    """Request to exchange the shared token for a JWT."""

    shared_token: str = Field(
        ..., min_length=8, description="Shared token for authentication"
    )

    model_config = {"from_attributes": True}


class TokenCreateResponse(BaseModel):
    """A newly issued access token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int

    model_config = {"from_attributes": True}


class TokenRefreshRequest(BaseModel):
    """Request to refresh an existing access token."""

    token: str = Field(..., min_length=8, description="Current valid token")

    model_config = {"from_attributes": True}


class TokenRefreshResponse(BaseModel):
    """A refreshed access token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int

    model_config = {"from_attributes": True}
