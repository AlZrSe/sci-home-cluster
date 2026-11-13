"""Authentication helpers for MCP transports."""

import os
from typing import Any, Dict, Optional

from backend.core.config import settings
from backend.core.security import decode_access_token
from backend.core.utils import is_localhost, is_loopback_ip


def resolve_stdio_payload() -> Dict[str, Any]:
    """Resolve the auth payload for a stdio MCP server process."""
    token = (
        os.environ.get("SHC_MCP_TOKEN")
        or os.environ.get("SHC_TOKEN")
        or settings.SHC_MCP_TOKEN
    )
    if token:
        try:
            return decode_access_token(token)
        except Exception:
            # Fall through to bypass logic if token invalid? Prefer explicit error.
            raise
    # Local stdio is treated as a trusted local client (localhost bypass).
    if settings.LOCALHOST_BYPASS:
        return {"sub": "localhost_user"}
    raise RuntimeError(
        "MCP stdio server requires SHC_MCP_TOKEN/SHC_TOKEN or localhost bypass"
    )


def validate_http_token(request) -> Dict[str, Any]:
    """Validate a token from an HTTP request (FastAPI Request or similar).

    Mirrors backend.core.deps.get_current_token_payload but is callable from
    transport code. Returns the decoded payload or raises.
    """

    # This is an async function in deps; callers should await it. Provide sync
    # wrapper not needed; expose async helper instead.
    raise NotImplementedError("use get_current_token_payload dependency")


async def http_payload_from_request(request) -> Dict[str, Any]:
    from backend.core.deps import get_current_token_payload

    return await get_current_token_payload(request)


def localhost_bypass_payload_for_host(
    host: Optional[str], client_host: Optional[str]
) -> Optional[Dict[str, Any]]:
    mode = settings.localhost_bypass_mode
    if mode.value == "disabled":
        return None
    if mode.value == "socket":
        if is_loopback_ip(client_host):
            return {"sub": "localhost_user"}
    elif mode.value == "host_header":
        if is_localhost(host):
            return {"sub": "localhost_user"}
    return None
