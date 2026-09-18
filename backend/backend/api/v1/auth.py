"""
Authentication API endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from backend.core.deps import get_current_token_payload
from backend.core.security import create_access_token
from backend.core.config import settings

router = APIRouter()


@router.post("/login")
async def login():
    """
    Authenticate user and return access token.
    """
    # TODO: Implement login logic
    # For now, we return a fixed token for development
    access_token = create_access_token(
        data={"sub": "test_user"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.post("/logout")
async def logout():
    """
    Logout user and invalidate token.
    """
    # TODO: Implement logout logic
    return {"message": "Logout endpoint - to be implemented"}


@router.get("/verify")
async def verify_token(payload: dict = Depends(get_current_token_payload)):
    """
    Verify the token and return whether it is valid.
    """
    return {"valid": True, "payload": payload}