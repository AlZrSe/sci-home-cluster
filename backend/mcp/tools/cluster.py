"""MCP tools for cluster health."""

from pathlib import Path

from backend.core.config import settings
from backend.store import get_store


async def cluster_health(ctx, args: dict) -> dict:
    store = get_store()
    nodes = await store.list_nodes()
    jobs, total = await store.list_jobs()
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "store": {"status": "healthy", "nodes": len(nodes), "jobs": len(jobs)},
        "config": {
            "status": "healthy" if settings.SYNCTHING_ROOT else "degraded",
            "syncthing_root": settings.SYNCTHING_ROOT,
        },
        "syncthing": {
            "status": "available"
            if Path(settings.SYNCTHING_ROOT).exists()
            else "not_configured",
            "path": settings.SYNCTHING_ROOT,
        },
    }


def register(server):
    from backend.mcp.server import MCPServer  # noqa

    server.register_tool(
        "cluster.health",
        "Get cluster health status.",
        {"type": "object", "properties": {}},
        cluster_health,
    )
