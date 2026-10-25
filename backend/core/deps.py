from typing import Optional

from fastapi import Depends, Request, Response, WebSocket, WebSocketException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.core.config import settings, LocalhostBypassMode
from backend.core.errors import auth_token_expired, auth_token_missing
from backend.core.security import decode_access_token
from backend.core.utils import is_localhost, is_loopback_ip
from backend.core.ws_tickets import validate_ws_ticket
import logging

logger = logging.getLogger(__name__)

reusable_oauth2 = HTTPBearer(
    scheme_name="Authorization",
    description="Enter your bearer token in the format **Bearer <token>**",
    auto_error=False,
)


def _check_localhost_bypass_http(request: Request) -> Optional[dict]:
    """
    Check if localhost bypass should activate for HTTP request.

    Returns a fake payload if bypass activates, None otherwise.
    """
    if settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.DISABLED:
        return None

    client_host = request.client.host if request.client else None

    if settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET:
        # NEW: Trust the socket peer address
        if is_loopback_ip(client_host):
            logger.info(
                "Auth bypass activated for %s via socket (path: %s, ua: %s)",
                client_host,
                request.url.path,
                request.headers.get("user-agent", "unknown"),
            )
            return {"sub": "localhost_user"}

    elif settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.HOST_HEADER:
        # DEPRECATED: Trust the client-supplied Host header
        hostname = request.url.hostname
        if is_localhost(hostname):
            logger.warning(
                "Auth bypass activated for %s via host_header "
                "(DEPRECATED, path: %s, ua: %s)",
                client_host,
                request.url.path,
                request.headers.get("user-agent", "unknown"),
            )
            return {"sub": "localhost_user"}

    return None


def _check_localhost_bypass_ws(websocket: WebSocket) -> Optional[dict]:
    """
    Check if localhost bypass should activate for WebSocket connection.

    Returns a fake payload if bypass activates, None otherwise.
    """
    if settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.DISABLED:
        return None

    client_host = websocket.client.host if websocket.client else None

    if settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET:
        # NEW: Trust the socket peer address
        if is_loopback_ip(client_host):
            logger.info(
                "WS auth bypass activated for %s via socket (path: %s)",
                client_host,
                websocket.url.path,
            )
            return {"sub": "localhost_user"}

    elif settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.HOST_HEADER:
        # DEPRECATED: Trust the client-supplied Host header
        hostname = websocket.url.hostname
        if is_localhost(hostname):
            logger.warning(
                "WS auth bypass activated for %s via host_header "
                "(DEPRECATED, path: %s)",
                client_host,
                websocket.url.path,
            )
            return {"sub": "localhost_user"}

    return None


async def get_current_token_payload(
    request: Request,
    token: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> dict:
    """
    Validate the token and return the payload.

    If LOCALHOST_BYPASS_MODE is not disabled and conditions are met,
    return a fake payload.
    """
    # Check if we are in localhost bypass mode
    bypass_payload = _check_localhost_bypass_http(request)
    if bypass_payload is not None:
        return bypass_payload

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

    Extracts token from:
    1. Authorization header (preferred for non-browser clients)
    2. Sec-WebSocket-Protocol subprotocol: ["bearer", "<token>"] (legacy JWT)
    3. Sec-WebSocket-Protocol subprotocol: ["bearer", "<ticket>"]
       (new WebSocket ticket)

    Query parameter authentication is NOT supported
    (security: tokens in URLs leak).

    If LOCALHOST_BYPASS_MODE is not disabled and conditions are met,
    return a fake payload.
    """
    # Check if we are in localhost bypass mode
    bypass_payload = _check_localhost_bypass_ws(websocket)
    if bypass_payload is not None:
        return bypass_payload

    # FIRST: Check Authorization header (preferred for non-browser clients)
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
    else:
        token = None

    # SECOND: Check Sec-WebSocket-Protocol subprotocol for ticket or legacy token
    if not token:
        token = _token_from_subprotocol(websocket)

    # THIRD: Check for WebSocket ticket (new format: ws_...)
    if not token:
        ticket = _ticket_from_subprotocol(websocket)
        if ticket and validate_ws_ticket(ticket):
            return {"sub": "user", "auth_method": "ws_ticket"}

    # FOURTH: Reject query parameter tokens with clear error
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


def _ticket_from_subprotocol(websocket: WebSocket) -> Optional[str]:
    """
    Extract a WebSocket ticket sent as a WebSocket subprotocol.

    The client offers e.g. ["bearer", "ws_<ticket>"]. We accept the handshake
    only if "bearer" was among the offered protocols.
    """
    offered = websocket.headers.get("sec-websocket-protocol")
    if not offered:
        return None
    protocols = [part.strip() for part in offered.split(",") if part.strip()]
    if "bearer" not in [p.lower() for p in protocols]:
        return None
    candidates = [p for p in protocols if p.lower() != "bearer" and p.startswith("ws_")]
    return candidates[0] if candidates else None


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


async def rate_limit_dependency(
    request: Request,
    response: Response,
) -> None:
    """
    FastAPI dependency for rate limiting auth endpoints.

    This dependency is DEPRECATED - rate limiting is now done directly in
    endpoint handlers to ensure headers are properly added to the final response.
    """
    pass
