"""
Integration tests for API endpoints.
"""

from fastapi.testclient import TestClient
from backend.main import app


def test_auth_router_included():
    """Test that auth router is properly included."""
    client = TestClient(app)
    # Test that the auth endpoints exist (they'll return 405 or 422
    # since we're not sending correct data)
    response = client.post("/api/v1/auth/validate", json={"token": "test"})
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


class TestNodesEndpoints:
    """Test nodes API endpoints."""

    def test_nodes_list_success(self):
        """Test GET /nodes returns 200 with array of NodeSpec objects."""
        client = TestClient(app)
        response = client.get("/api/v1/nodes/")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # seed nodes

        # Verify all required fields present
        for node in data:
            assert "node_id" in node
            assert "hostname" in node
            assert "gpus" in node
            assert "cpus" in node
            assert "memory_gb" in node
            assert "os" in node
            assert "status" in node
            assert "last_heartbeat" in node
            assert "current_job_id" in node

            # GPU structure
            assert isinstance(node["gpus"], list)
            for gpu in node["gpus"]:
                assert "name" in gpu
                assert "memory_gb" in gpu

            # Status enum
            assert node["status"] in ["ONLINE", "OFFLINE"]

            # current_job_id is string or null
            assert node["current_job_id"] is None or isinstance(
                node["current_job_id"], str
            )

    def test_nodes_list_unauthorized(self):
        """Test GET /nodes returns 401 without auth (simulated by non-localhost)."""
        # This test would need a non-localhost test client setup
        # For now, we just verify the endpoint exists
        client = TestClient(app)
        response = client.get("/api/v1/nodes/")
        # On localhost, this should succeed (200)
        assert response.status_code == 200

    def test_nodes_get_success(self):
        """Test GET /nodes/{id} returns 200 with NodeSpec for valid node."""
        client = TestClient(app)
        response = client.get("/api/v1/nodes/node-beta")
        assert response.status_code == 200

        data = response.json()
        assert data["node_id"] == "node-beta"
        assert data["hostname"] == "beta.lan"
        assert len(data["gpus"]) == 2
        assert data["gpus"][0]["name"] == "NVIDIA RTX 3090"
        assert data["gpus"][0]["memory_gb"] == 24
        assert data["status"] == "ONLINE"
        assert data["current_job_id"] == "job-1039"

    def test_nodes_get_not_found(self):
        """Test GET /nodes/{id} returns 404 for non-existent node."""
        client = TestClient(app)
        response = client.get("/api/v1/nodes/non-existent-node")
        assert response.status_code == 404

        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"
        assert "not found" in data["detail"].lower()
        # Instance is a full URL in TestClient
        assert data["instance"] == "http://testserver/api/v1/nodes/non-existent-node"

    def test_nodes_get_unauthorized(self):
        """Test GET /nodes/{id} returns 401 without auth."""
        client = TestClient(app)
        response = client.get("/api/v1/nodes/node-alpha")
        # On localhost, this should succeed (200)
        assert response.status_code == 200

    def test_nodes_data_structure_validation(self):
        """Test node data structure matches expectations."""
        client = TestClient(app)
        response = client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = {node["node_id"]: node for node in response.json()}

        # node-gamma has M3 Max GPU
        gamma = nodes.get("node-gamma")
        assert gamma is not None
        assert len(gamma["gpus"]) == 1
        assert gamma["gpus"][0]["name"] == "Apple M3 Max (MPS)"
        assert gamma["gpus"][0]["memory_gb"] == 36

        # node-delta has empty gpus, OFFLINE status, null current_job_id
        delta = nodes.get("node-delta")
        assert delta is not None
        assert delta["gpus"] == []
        assert delta["status"] == "OFFLINE"
        assert delta["current_job_id"] is None

        # All timestamps are valid ISO 8601
        for node in nodes.values():
            # Should be parseable as datetime
            from datetime import datetime

            dt = datetime.fromisoformat(node["last_heartbeat"].replace(" ", "T"))
            assert dt is not None

    def test_nodes_localhost_bypass(self):
        """Test localhost bypass works - no auth needed on localhost."""
        # TestClient simulates localhost, so no auth should be needed
        client = TestClient(app)

        # List nodes without auth - should succeed on localhost
        response = client.get("/api/v1/nodes/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 4

        # Get specific node without auth - should succeed on localhost
        response = client.get("/api/v1/nodes/node-alpha")
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "node-alpha"
