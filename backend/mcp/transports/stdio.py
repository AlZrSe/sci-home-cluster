"""Stdio transport for MCP: JSON-RPC over stdin/stdout (newline-delimited)."""

import asyncio
import json
import logging
import sys
from pathlib import Path

from backend.core.config import settings
from backend.core.database import init_database, close_database, run_migrations
from backend.store import get_store
from backend.services.syncthing_service import SyncthingService
from backend.mcp.auth import resolve_stdio_payload
from backend.mcp.context import MCPContext
from backend.mcp.factory import create_server
from backend.mcp.runtime import set_syncthing_service

logger = logging.getLogger("mcp.stdio")


async def _bootstrap():
    await init_database()
    await run_migrations()
    get_store()
    svc = None
    root = Path(settings.SYNCTHING_ROOT)
    if root.exists():
        svc = SyncthingService(root)
        await svc.start()
        set_syncthing_service(svc)
    return svc


async def _shutdown(svc):
    if svc is not None:
        await svc.stop()
    await close_database()


async def main_async():
    logging.basicConfig(level=logging.WARNING)
    svc = await _bootstrap()
    server = create_server()
    payload = resolve_stdio_payload()
    ctx = MCPContext(transport="stdio", payload=payload, syncthing_service=svc)
    # Read loop
    loop = asyncio.get_event_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue
            response = await server.handle(request, ctx)
            if response is not None:
                sys.stdout.write(json.dumps(response, default=str) + "\n")
                sys.stdout.flush()
    finally:
        await _shutdown(svc)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
