"""
Authentication API endpoints.
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Header
from backend.core.config import settings
from backend.core.deps import get_current_token_payload
from backend.models.token_validation import (
    TokenValidationRequest,
    TokenValidationResponse,
    TokenCreateRequest,
    TokenCreateResponse,
    TokenRefreshRequest,
    TokenRefreshResponse,
)
from backend.services.auth_service import AuthService

router = APIRouter()


@router.get("/verify", response_model=TokenValidationResponse)
async def verify_token(
    authorization: str = Header(None),
    payload: dict = Depends(get_current_token_payload),
):
    """
    Validate bearer token via GET with Authorization header.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return TokenValidationResponse(valid=False)

    token = authorization[7:]  # Remove "Bearer " prefix
    auth_service = AuthService()
    is_valid = await auth_service.validate_token(token)
    return TokenValidationResponse(valid=is_valid)


@router.post("/validate", response_model=TokenValidationResponse)
async def validate_token(
    request: TokenValidationRequest,
    payload: dict = Depends(get_current_token_payload),
):
    """
    Validate bearer token via POST with JSON body.
    """
    auth_service = AuthService()
    is_valid = await auth_service.validate_token(request.token)
    return TokenValidationResponse(valid=is_valid)


@router.post("/token", response_model=TokenCreateResponse)
async def create_token(request: TokenCreateRequest):
    """
    Create a new JWT access token by validating against shared token.

    This endpoint accepts a shared token and returns a JWT token if valid.
    """
    auth_service = AuthService()

    # Validate the shared token
    if not auth_service.get_shared_token():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Shared token is not configured on the server. "
                "Set the SHARED_TOKEN environment variable and restart the API server."
            ),
        )

    is_valid = await auth_service.verify_shared_token(request.shared_token)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "The provided shared token is invalid. "
                "Please check your credentials and try again."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new JWT token
    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = await auth_service.create_token(expires_delta=expires_delta)

    return TokenCreateResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenRefreshResponse)
async def refresh_token(request: TokenRefreshRequest):
    """
    Refresh an existing JWT token.

    Returns a new token with extended expiration if the current token is valid.
    """
    auth_service = AuthService()

    new_token = await auth_service.refresh_token(request.token)
    if not new_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Your session has expired or the token is invalid. "
                "Please log in again to get a new access token."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenRefreshResponse(
        access_token=new_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
