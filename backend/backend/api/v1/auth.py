"""
Authentication API endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from backend.core.deps import get_current_token_payload
from backend.models.token_validation import TokenValidationRequest, TokenValidationResponse
from backend.services.auth_service import AuthService

router = APIRouter()


@router.post("/validate", response_model=TokenValidationResponse)
async def validate_token(
    request: TokenValidationRequest,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Validate bearer token.
    """
    # TODO: Implement actual token validation logic
    # For now, just check if token is not empty
    auth_service = AuthService()
    is_valid = await auth_service.validate_token(request.token)
    return TokenValidationResponse(valid=is_valid)