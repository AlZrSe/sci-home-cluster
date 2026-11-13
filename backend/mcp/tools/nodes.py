"""MCP tools for node management."""

from shared.schemas.node_spec import NodeSpec
from backend.services.node_service import NodeService


async def nodes_list(ctx, args: dict) -> list:
    service = NodeService()
    nodes = await service.list_nodes()
    return [n.model_dump(mode="json") for n in nodes]


async def nodes_get(ctx, args: dict) -> dict:
    service = NodeService()
    node = await service.get_node(args["node_id"])
    if node is None:
        raise ValueError("node_not_found")
    return node.model_dump(mode="json")


async def nodes_metrics(ctx, args: dict) -> dict:
    service = NodeService()
    metrics = await service.get_node_metrics(args["node_id"])
    if metrics is None:
        raise ValueError("node_metrics_not_found")
    return metrics.model_dump(mode="json")


async def nodes_create(ctx, args: dict) -> dict:
    spec_data = args["node_spec"]
    if isinstance(spec_data, dict):
        spec = NodeSpec(**spec_data)
    else:
        spec = spec_data  # type: ignore[assignment]
    service = NodeService()
    created = await service.create_node(spec)
    return created.model_dump(mode="json")


def register(server):
    server.register_tool(
        "nodes.list",
        "List all cluster nodes.",
        {"type": "object", "properties": {}},
        nodes_list,
    )
    server.register_tool(
        "nodes.get",
        "Get node details.",
        {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
        nodes_get,
    )
    server.register_tool(
        "nodes.metrics",
        "Get node GPU/CPU metrics.",
        {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
        nodes_metrics,
    )
    server.register_tool(
        "nodes.create",
        "Create a new node (test seeding).",
        {
            "type": "object",
            "properties": {"node_spec": NodeSpec.model_json_schema()},
            "required": ["node_spec"],
        },
        nodes_create,
        mutating=True,
    )
