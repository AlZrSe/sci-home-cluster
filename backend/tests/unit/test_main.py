"""
Integration tests for the main application.
"""

from fastapi.testclient import TestClient
from backend.backend.main import app


def test_root_endpoint():
    """Test the root endpoint returns correct message."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to Scientific Home Cluster API"}


def test_health_endpoint():
    """Test the health endpoint returns correct status."""
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "1.0.0"