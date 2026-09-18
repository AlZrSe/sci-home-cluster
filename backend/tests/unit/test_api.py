"""
Integration tests for API endpoints.
"""

from fastapi.testclient import TestClient
from backend.backend.main import app


def test_auth_router_included():
    """Test that auth router is properly included."""
    client = TestClient(app)
    # Test that the auth endpoints exist (they'll return 405 or 422 since we're not sending correct data)
    response = client.post("/api/v1/auth/login")
    # Should not be 404 (which would mean router not included)
    assert response.status_code != 404


def test_jobs_router_included():
    """Test that jobs router is properly included."""
    client = TestClient(app)
    response = client.get("/api/v1/jobs/")
    assert response.status_code != 404


def test_nodes_router_included():
    """Test that nodes router is properly included."""
    client = TestClient(app)
    response = client.get("/api/v1/nodes/")
    assert response.status_code != 404