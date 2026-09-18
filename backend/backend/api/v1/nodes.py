"""
Node management API endpoints.
"""

from fastapi import APIRouter, Depends
from backend.core.deps import get_current_token_payload

router = APIRouter()


@router.get("/")
async def list_nodes(payload: dict = Depends(get_current_token_payload)):
    """
    List all nodes.
    """
    # TODO: Implement node listing
    return {"message": "List nodes endpoint - to be implemented"}


@router.post("/")
async def register_node(payload: dict = Depends(get_current_token_payload)):
    """
    Register a new node.
    """
    # TODO: Implement node registration
    return {"message": "Register node endpoint - to be implemented"}


@router.get("/{node_id}")
async def get_node(node_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get a specific node by ID.
    """
    # TODO: Implement get node
    return {"message": f"Get node {node_id} endpoint - to be implemented"}


@router.delete("/{node_id}")
async def deregister_node(node_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Deregister a node by ID.
    """
    # TODO: Implement node deregistration
    return {"message": f"Deregister node {node_id} endpoint - to be implemented"}