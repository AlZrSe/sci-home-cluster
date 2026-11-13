"""SSE/HTTP transport for MCP (mounted in FastAPI)."""

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from backend.core.config import settings
from backend.core.deps import get_current_token_payload
from backend.mcp.context import MCPContext
from backend.mcp.factory import create_server
from backend.mcp.runtime import set_syncthing_service, get_syncthing_service

logger = logging.getLogger("mcp.sse")

router = APIRouter(include_in_schema=False)

# Shared server instance (created on first use)
_server = None
_sessions: Dict[str, asyncio.Queue] = {}


def get_server():
    global _server
    if _server is None:
        _server = create_server()
    return _server


async def _sse_event_generator(session_id: str, request: Request):
    queue = _sessions[session_id]
    # Send endpoint event
    endpoint = f"{settings.MCP_SSE_PATH}/messages?session_id={session_id}"
    yield f"event: endpoint\ndata: {endpoint}\n\n"
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # keepalive
                yield ": keepalive\n\n"
                continue
            yield f"event: message\ndata: {json.dumps(msg, default=str)}\n\n"
    finally:
        _sessions.pop(session_id, None)


@router.get("/sse")
async def sse_endpoint(
    request: Request,
    payload: dict = Depends(get_current_token_payload),
):
    session_id = str(uuid.uuid4())
    _sessions[session_id] = asyncio.Queue()
    # store syncthing service on a global for tool access; also pass via context later
    svc = getattr(request.app.state, "syncthing_service", None)
    if svc is not None:
        set_syncthing_service(svc)
    return StreamingResponse(
        _sse_event_generator(session_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/messages")
async def messages_endpoint(
    request: Request,
    session_id: Optional[str] = Query(None),
    payload: dict = Depends(get_current_token_payload),
):
    server = get_server()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )

    ctx = MCPContext(
        transport="sse",
        payload=payload,
        syncthing_service=getattr(request.app.state, "syncthing_service", None)
        or get_syncthing_service(),
    )

    # Handle batch or single
    if isinstance(body, list):
        responses = []
        for req in body:
            r = await server.handle(req, ctx)
            if r is not None:
                responses.append(r)
        # push to session queue if present
        if session_id and session_id in _sessions:
            for r in responses:
                await _sessions[session_id].put(r)
        return JSONResponse(responses)
    else:
        r = await server.handle(body, ctx)
        if session_id and session_id in _sessions and r is not None:
            await _sessions[session_id].put(r)
        if r is None:
            return Response(status_code=202)
        return JSONResponse(r)


@router.post("")
async def messages_root(
    request: Request,
    payload: dict = Depends(get_current_token_payload),
):
    # Allow POST to base path as streamable HTTP
    return await messages_endpoint(request, session_id=None, payload=payload)
