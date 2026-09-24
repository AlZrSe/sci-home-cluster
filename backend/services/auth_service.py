"""
Auth service layer containing business logic for authentication.
"""

from datetime import timedelta
from typing import Optional
from jose import JWTError

from backend.core.config import settings
from backend.core.security import create_access_token, decode_access_token


class AuthService:
    """Authentication service for token management."""

    def __init__(self):
        pass

    @property
    def _shared_token(self) -> Optional[str]:
        """Get the current shared token from settings."""
        return settings.SHARED_TOKEN

    async def validate_token(self, token: str) -> bool:
        """
        Validate bearer token against shared token or JWT.

        Args:
            token: The bearer token to validate

        Returns:
            True if valid, False otherwise
        """
        if not token or len(token) < 8:
            return False

        # Check against shared token (for simple bearer token auth)
        if self._shared_token and token == self._shared_token:
            return True

        # Try to validate as JWT
        try:
            _ = decode_access_token(token)
            # Check expiration is already done in decode_access_token
            return True
        except JWTError:
            return False
        except Exception:
            return False

    async def create_token(
        self, subject: str = "user", expires_delta: Optional[timedelta] = None
    ) -> str:
        """
        Create a new JWT access token.

        Args:
            subject: The subject (user identifier) for the token
            expires_delta: Optional custom expiration

        Returns:
            JWT token string
        """
        data = {"sub": subject}
        return create_access_token(data, expires_delta)

    async def refresh_token(self, token: str) -> Optional[str]:
        """
        Refresh an existing token by creating a new one with same subject.

        Args:
            token: The current valid token

        Returns:
            New token string if valid, None otherwise
        """
        if not await self.validate_token(token):
            return None

        try:
            payload = decode_access_token(token)
            subject = payload.get("sub", "user")
            # Create new token with fresh expiration (add 1 second to ensure difference)
            return await self.create_token(
                subject,
                expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            )
        except Exception:
            return None

    def get_shared_token(self) -> Optional[str]:
        """Get the configured shared token."""
        return self._shared_token

    async def verify_shared_token(self, token: str) -> bool:
        """Verify token against shared token only (not JWT)."""
        if not self._shared_token:
            return False
        return token == self._shared_token
