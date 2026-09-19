"""
Node management API endpoints.
"""

from typing import List
from fastapi import APIRouter, Depends
from backend.core.deps import get_current_token_payload
from backend.models.node_spec import NodeSpec
from backend.services.node_service import NodeService

router = APIRouter()


@router.get("/", response_model=List[NodeSpec])
async def list_nodes(
    payload: dict = Depends(get_current_token_payload)
):
    """
    List all cluster nodes.
    """
    # TODO: Implement actual node listing logic
    node_service = NodeService()
    nodes = await node_service.list_nodes()
    return nodes


@router.get("/{node_id}", response_model=NodeSpec)
async def get_node(
    node_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Get node details.
    """
    # TODO: Implement get node logic
    node_service = NodeService()
    node = await node_service.get_node(node_id)
    return node