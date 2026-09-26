"""
Integration tests for the main application.
"""

from fastapi.testclient import TestClient
from fastapi import HTTPException
from backend.main import app
from backend.core.config import settings
from backend.models.error_response import ErrorResponse


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


def test_http_exception_handler():
    """Test that HTTPException is converted to ErrorResponse format."""
    client = TestClient(app)
    response = client.get("/api/v1/jobs/nonexistent-job-id")
    # Job not found returns 404 with ErrorResponse
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == 404
    assert data["title"] == "Not Found"
    assert "error_code" in data
    assert data["error_code"] == "JOB_NOT_FOUND"
    assert "instance" in data


def test_validation_exception_handler():
    """Test that validation errors return ErrorResponse format."""
    client = TestClient(app)
    # Send invalid token (too short) to trigger validation error
    response = client.post(
        "/api/v1/auth/validate",
        json={"token": "short"},
        headers={"Authorization": "Bearer localhost-no-auth"},
    )
    assert response.status_code == 422
    data = response.json()
    assert data["status"] == 422
    assert data["title"] == "Unprocessable Entity"
    assert "error_code" in data
    assert data["error_code"] == "VALIDATION_FAILED"
    assert "instance" in data


def test_request_id_middleware():
    """Test that X-Request-ID is added to responses."""
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert "x-request-id" in response.headers
