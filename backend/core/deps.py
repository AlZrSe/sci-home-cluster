from fastapi import Depends, Request, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.core.config import settings
from backend.core.errors import auth_token_expired, auth_token_missing
from backend.core.security import decode_access_token
from backend.core.utils import is_localhost

reusable_oauth2 = HTTPBearer(
    scheme_name="Authorization",
    description="Enter your bearer token in the format **Bearer <token>**",
    auto_error=False,
)


async def get_current_token_payload(
    request: Request,
    token: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> dict:
    """
    Validate the token and return the payload.
    If LOCALHOST_BYPASS is True and the host is local, return a fake payload.
    """
    # Check if we are in localhost bypass mode
    if settings.LOCALHOST_BYPASS:
        # Get the hostname from the request
        hostname = request.url.hostname
        if is_localhost(hostname):
            # Bypass authentication: return a fixed payload
            return {"sub": "localhost_user"}

    # If not bypassing, then we require a token
    if token is None:
        raise auth_token_missing(
            "Authentication required. Please provide a valid bearer token in "
            "the Authorization header."
        )

    try:
        payload = decode_access_token(token.credentials)
        return payload
    except Exception:
        raise auth_token_expired(
            "Invalid or expired token. Please log in again to get a new access token."
        )


async def get_ws_token_payload(
    websocket: WebSocket,
) -> dict:
    """
    Validate the token for WebSocket connections and return the payload.
    Extracts token from query params (?token=xxx) or Authorization header.
    If LOCALHOST_BYPASS is True and the host is local, return a fake payload.
    """
    # Check if we are in localhost bypass mode
    if settings.LOCALHOST_BYPASS:
        # Get the hostname from the websocket
        hostname = websocket.url.hostname
        if is_localhost(hostname):
            # Bypass authentication: return a fixed payload
            return {"sub": "localhost_user"}

    # Extract token from query parameters
    token = websocket.query_params.get("token")

    # If not in query params, try Authorization header
    if not token:
        auth_header = websocket.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix

    if not token:
        raise auth_token_missing(
            "Authentication required. Please provide a valid bearer token."
        )

    try:
        payload = decode_access_token(token)
        return payload
    except Exception:
        raise auth_token_expired(
            "Invalid or expired token. Please log in again to get a new access token."
        )
