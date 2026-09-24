"""
Integration tests for the main application.
"""

from fastapi.testclient import TestClient
from backend.main import app
from backend.core.config import settings


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
    assert data["version"] == settings.VERSION
    # Check that all required health check sections are present
    assert "store" in data
    assert "config" in data
    assert "syncthing" in data
    assert "database" in data
