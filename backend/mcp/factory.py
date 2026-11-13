"""Factory for building a configured MCPServer."""

from backend.mcp.server import MCPServer
from backend.mcp.tools.jobs import register as reg_jobs
from backend.mcp.tools.nodes import register as reg_nodes
from backend.mcp.tools.syncthing import register as reg_sync
from backend.mcp.tools.cluster import register as reg_cluster
from backend.mcp.resources import register as reg_resources
from backend.mcp.prompts import register as reg_prompts


def create_server() -> MCPServer:
    server = MCPServer()
    reg_jobs(server)
    reg_nodes(server)
    reg_sync(server)
    reg_cluster(server)
    reg_resources(server)
    reg_prompts(server)
    return server
