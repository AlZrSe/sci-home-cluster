"""MCP tools for cluster health."""


async def cluster_health(ctx, args: dict) -> dict:
    from backend.main import health_check

    result = await health_check()
    return dict(result)


def register(server):
    server.register_tool(
        "cluster.health",
        "Get cluster health status.",
        {"type": "object", "properties": {}},
        cluster_health,
    )
