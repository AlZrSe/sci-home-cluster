"""
Node service layer containing business logic for node management.
"""

from typing import List, Optional
from backend.store.memory import get_store
from backend.models.node_spec import NodeSpec


class NodeService:
    def __init__(self):
        # Initialize the store
        self._store = get_store()
    
    async def list_nodes(self) -> List[NodeSpec]:
        """List all cluster nodes."""
        return await self._store.list_nodes()
    
    async def get_node(self, node_id: str) -> Optional[NodeSpec]:
        """Get a node by ID."""
        return await self._store.get_node(node_id)

    async def update_node(self, node_id: str, **kwargs) -> Optional[NodeSpec]:
        """Update a node's fields and return the updated node."""
        return await self._store.update_node(node_id, **kwargs)