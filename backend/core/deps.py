from fastapi import Depends, HTTPException, Request, status, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.core.config import settings
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(token.credentials)
        return payload
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(token)
        return payload
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
