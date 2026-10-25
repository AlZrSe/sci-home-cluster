"""
WebSocket ticket management for secure WebSocket authentication.

Implements single-use, short-lived tickets for WebSocket connections
to avoid passing long-lived tokens in WebSocket handshakes.
"""

import secrets
import time
from typing import Dict

from backend.core.config import settings


# In-memory store for WebSocket tickets (single-use, short-lived)
# Format: {ticket: {"expires_at": float, "used": bool}}
_ws_tickets: Dict[str, Dict[str, float | bool]] = {}


def _cleanup_expired_tickets() -> None:
    """Remove expired tickets from the store."""
    now = time.time()
    expired = [
        ticket
        for ticket, data in _ws_tickets.items()
        if data.get("expires_at", 0) < now
    ]
    for ticket in expired:
        del _ws_tickets[ticket]


def create_ws_ticket() -> str:
    """Create a new WebSocket ticket."""
    _cleanup_expired_tickets()
    ticket = f"ws_{secrets.token_urlsafe(32)}"
    expires_at = time.time() + settings.AUTH_WS_TICKET_TTL_SECONDS
    _ws_tickets[ticket] = {"expires_at": expires_at, "used": False}
    return ticket


def validate_ws_ticket(ticket: str) -> bool:
    """Validate and consume a WebSocket ticket.

    Returns True if valid and unused, False otherwise.
    """
    _cleanup_expired_tickets()
    data = _ws_tickets.get(ticket)
    if not data:
        return False
    if data.get("used", False):
        return False
    if data.get("expires_at", 0) < time.time():
        del _ws_tickets[ticket]
        return False
    # Mark as used (single-use)
    data["used"] = True
    return True


def get_ws_ticket_ttl() -> int:
    """Get the WebSocket ticket TTL in seconds."""
    return settings.AUTH_WS_TICKET_TTL_SECONDS
