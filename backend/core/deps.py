from typing import Optional

from fastapi import Depends, Request, WebSocket, WebSocketException, status
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
    Extracts token from Authorization header or Sec-WebSocket-Protocol subprotocol.
    Query parameter authentication is NOT supported (security: tokens in URLs leak).
    If LOCALHOST_BYPASS is True and the host is local, return a fake payload.
    """
    # Check if we are in localhost bypass mode
    if settings.LOCALHOST_BYPASS:
        # Get the hostname from the websocket
        hostname = websocket.url.hostname
        if is_localhost(hostname):
            # Bypass authentication: return a fixed payload
            return {"sub": "localhost_user"}

    # FIRST: Check Authorization header (preferred for non-browser clients)
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
    else:
        token = None

    # SECOND: Check Sec-WebSocket-Protocol subprotocol (required for browsers)
    if not token:
        token = _token_from_subprotocol(websocket)

    # THIRD: Reject query parameter tokens with clear error
    query_token = websocket.query_params.get("token")
    if query_token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=(
                "Authentication required. Query parameter authentication "
                "is not supported. Use Authorization header or WebSocket "
                "subprotocol."
            ),
        )

    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Authentication required. Please provide a valid bearer token.",
        )

    try:
        payload = decode_access_token(token)
        return payload
    except Exception:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=(
                "Invalid or expired token. Please log in again "
                "to get a new access token."
            ),
        )


def _token_from_subprotocol(websocket: WebSocket) -> Optional[str]:
    """
    Extract a token sent as a WebSocket subprotocol.

    The client offers e.g. ["bearer", "<token>"]. We accept the handshake
    only if "bearer" was among the offered protocols, so that echoing the
    accepted subprotocol stays a valid token per RFC 6455.
    """
    offered = websocket.headers.get("sec-websocket-protocol")
    if not offered:
        return None
    protocols = [part.strip() for part in offered.split(",") if part.strip()]
    if "bearer" not in [p.lower() for p in protocols]:
        return None
    candidates = [p for p in protocols if p.lower() != "bearer"]
    return candidates[0] if candidates else None


def ws_accepted_subprotocol(websocket: WebSocket) -> Optional[str]:
    """
    The subprotocol to echo back when accepting the handshake.

    Returns "bearer" when the client offered it, so the browser's
    WebSocket object reports the connection as open rather than failing the
    protocol check.
    """
    offered = websocket.headers.get("sec-websocket-protocol")
    if not offered:
        return None
    for part in offered.split(","):
        if part.strip().lower() == "bearer":
            return "bearer"
    return None
