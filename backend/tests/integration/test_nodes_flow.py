"""
Integration tests for node listing and detail endpoints.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app


@pytest.mark.integration
class TestNodesFlow:
    """Integration tests for node management endpoints."""

    @pytest.fixture
    async def async_client(self) -> AsyncClient:
        """Create an async client for testing with localhost bypass."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_list_nodes(self, async_client):
        """Test listing all nodes returns seed data."""
        # Act
        response = await async_client.get("/api/v1/nodes/")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # At least 4 seed nodes

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

    @pytest.mark.asyncio
    async def test_list_nodes_structure_validation(self, async_client):
        """Test node data structure matches expected seed data."""
        # Act
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = {node["node_id"]: node for node in response.json()}

        # node-alpha
        alpha = nodes.get("node-alpha")
        assert alpha is not None
        assert alpha["hostname"] == "alpha.lan"
        assert alpha["status"] == "ONLINE"
        assert len(alpha["gpus"]) == 1
        assert alpha["gpus"][0]["name"] == "NVIDIA RTX 4090"
        assert alpha["gpus"][0]["memory_gb"] == 24
        assert alpha["cpus"] == 16
        assert alpha["memory_gb"] == 64
        assert alpha["current_job_id"] == "job-1041"

        # node-beta
        beta = nodes.get("node-beta")
        assert beta is not None
        assert beta["hostname"] == "beta.lan"
        assert beta["status"] == "ONLINE"
        assert len(beta["gpus"]) == 2
        assert beta["gpus"][0]["name"] == "NVIDIA RTX 3090"
        assert beta["gpus"][0]["memory_gb"] == 24
        assert beta["cpus"] == 24
        assert beta["memory_gb"] == 128
        assert beta["current_job_id"] == "job-1039"

        # node-gamma (Mac with MPS)
        gamma = nodes.get("node-gamma")
        assert gamma is not None
        assert gamma["hostname"] == "gamma.lan"
        assert gamma["status"] == "ONLINE"
        assert len(gamma["gpus"]) == 1
        assert gamma["gpus"][0]["name"] == "Apple M3 Max (MPS)"
        assert gamma["gpus"][0]["memory_gb"] == 36
        assert gamma["cpus"] == 14
        assert gamma["memory_gb"] == 36
        assert gamma["current_job_id"] is None

        # node-delta (offline, no GPUs)
        delta = nodes.get("node-delta")
        assert delta is not None
        assert delta["hostname"] == "delta.lan"
        assert delta["status"] == "OFFLINE"
        assert delta["gpus"] == []
        assert delta["cpus"] == 8
        assert delta["memory_gb"] == 32
        assert delta["current_job_id"] is None

    @pytest.mark.asyncio
    async def test_get_node_success(self, async_client):
        """Test getting a specific node by ID."""
        # Act
        response = await async_client.get("/api/v1/nodes/node-beta")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "node-beta"
        assert data["hostname"] == "beta.lan"
        assert len(data["gpus"]) == 2
        assert data["gpus"][0]["name"] == "NVIDIA RTX 3090"
        assert data["gpus"][0]["memory_gb"] == 24
        assert data["status"] == "ONLINE"
        assert data["current_job_id"] == "job-1039"

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, async_client):
        """Test getting a non-existent node returns 404."""
        # Act
        response = await async_client.get("/api/v1/nodes/non-existent-node")

        # Assert
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"
        assert "does not exist" in data["detail"].lower()
        assert "error_code" in data
        # Instance should be a full URL
        assert data["instance"] == "http://testserver/api/v1/nodes/non-existent-node"

    @pytest.mark.asyncio
    async def test_get_node_all_seed_nodes(self, async_client):
        """Test getting each seed node individually."""
        node_ids = ["node-alpha", "node-beta", "node-gamma", "node-delta"]

        for node_id in node_ids:
            response = await async_client.get(f"/api/v1/nodes/{node_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["node_id"] == node_id

    @pytest.mark.asyncio
    async def test_node_timestamps_valid_iso(self, async_client):
        """Test that all node timestamps are valid ISO 8601 format."""
        # Act
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = response.json()
        from datetime import datetime

        for node in nodes:
            timestamp = node["last_heartbeat"]
            # Should be parseable as datetime (handle space separator)
            dt = datetime.fromisoformat(timestamp.replace(" ", "T"))
            assert dt is not None

    @pytest.mark.asyncio
    async def test_node_gpu_structure(self, async_client):
        """Test GPU structure in node responses."""
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = response.json()
        for node in nodes:
            gpus = node["gpus"]
            assert isinstance(gpus, list)
            for gpu in gpus:
                assert isinstance(gpu, dict)
                assert "name" in gpu
                assert "memory_gb" in gpu
                assert isinstance(gpu["name"], str)
                assert isinstance(gpu["memory_gb"], int)
                assert gpu["memory_gb"] > 0


@pytest.mark.integration
class TestNodesAuthentication:
    """Tests for authentication on node endpoints."""

    @pytest.fixture
    async def unauthenticated_client(self) -> AsyncClient:
        """Create an async client without auth."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    @pytest.mark.asyncio
    async def test_list_nodes_localhost_bypass(self, unauthenticated_client):
        """Test that localhost bypass works for nodes endpoints."""
        # TestClient simulates localhost, so no auth should be needed
        response = await unauthenticated_client.get("/api/v1/nodes/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 4

    @pytest.mark.asyncio
    async def test_get_node_localhost_bypass(self, unauthenticated_client):
        """Test that localhost bypass works for get node endpoint."""
        response = await unauthenticated_client.get("/api/v1/nodes/node-alpha")
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "node-alpha"


@pytest.mark.integration
class TestNodesValidation:
    """Tests for node data validation and edge cases."""

    @pytest.fixture
    async def async_client(self) -> AsyncClient:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_node_consistency_between_list_and_get(self, async_client):
        """Test that list and get return consistent data for the same node."""
        # Get list
        list_response = await async_client.get("/api/v1/nodes/")
        assert list_response.status_code == 200
        nodes_list = {n["node_id"]: n for n in list_response.json()}

        # Get each node individually and compare
        for node_id, node_data in nodes_list.items():
            get_response = await async_client.get(f"/api/v1/nodes/{node_id}")
            assert get_response.status_code == 200
            node_get = get_response.json()
            assert node_get == node_data

    @pytest.mark.asyncio
    async def test_empty_gpu_list_for_cpu_only_node(self, async_client):
        """Test that node-delta has empty GPU list."""
        response = await async_client.get("/api/v1/nodes/node-delta")
        assert response.status_code == 200
        data = response.json()
        assert data["gpus"] == []
        assert data["status"] == "OFFLINE"

    @pytest.mark.asyncio
    async def test_node_current_job_id_types(self, async_client):
        """Test that current_job_id is either string or null."""
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = response.json()
        for node in nodes:
            current_job_id = node["current_job_id"]
            assert current_job_id is None or isinstance(current_job_id, str)
            if current_job_id:
                assert current_job_id.startswith("job-")

    @pytest.mark.asyncio
    async def test_node_status_values(self, async_client):
        """Test that node status values are valid."""
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = response.json()
        for node in nodes:
            assert node["status"] in ["ONLINE", "OFFLINE"]

    @pytest.mark.asyncio
    async def test_node_hostname_format(self, async_client):
        """Test that hostnames follow expected format."""
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = response.json()
        for node in nodes:
            hostname = node["hostname"]
            assert isinstance(hostname, str)
            assert len(hostname) > 0
            # Should contain a dot (e.g., alpha.lan)
            assert "." in hostname


@pytest.mark.integration
class TestNodeMetrics:
    """Integration tests for GET /nodes/{id}/metrics endpoint."""

    @pytest.fixture
    async def auth_client(self) -> AsyncClient:
        """Create an async client with localhost bypass auth."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_get_node_metrics_success(self, auth_client):
        """Test GET /nodes/{id}/metrics returns metrics for existing node."""
        # Use a known seed node with GPUs
        response = await auth_client.get("/api/v1/nodes/node-alpha/metrics")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "node:node-alpha"
        assert "gpu_metrics" in data
        assert "cpu_metrics" in data
        assert "summary" in data
        assert len(data["gpu_metrics"]) > 0
        assert len(data["cpu_metrics"]) > 0

    @pytest.mark.asyncio
    async def test_get_node_metrics_not_found(self, auth_client):
        """Test GET /nodes/{id}/metrics returns 404 for non-existent node."""
        response = await auth_client.get("/api/v1/nodes/non-existent-node/metrics")

        assert response.status_code == 404
        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"
        assert "not available" in data["detail"].lower()
        assert "error_code" in data

    @pytest.mark.asyncio
    async def test_get_node_metrics_structure(self, auth_client):
        """Test node metrics response matches JobMetrics schema."""
        response = await auth_client.get("/api/v1/nodes/node-alpha/metrics")

        assert response.status_code == 200
        data = response.json()

        # GPU metrics structure
        for gpu in data["gpu_metrics"]:
            assert "timestamp" in gpu
            assert "gpu_index" in gpu
            assert "memory_used_mb" in gpu
            assert "memory_total_mb" in gpu
            assert "utilization_percent" in gpu
            assert "temperature_c" in gpu
            assert isinstance(gpu["gpu_index"], int)
            assert isinstance(gpu["memory_used_mb"], int)
            assert isinstance(gpu["memory_total_mb"], int)
            assert isinstance(gpu["utilization_percent"], int)
            assert isinstance(gpu["temperature_c"], int)

        # CPU metrics structure
        for cpu in data["cpu_metrics"]:
            assert "timestamp" in cpu
            assert "cpu_percent" in cpu
            assert "memory_percent" in cpu
            assert isinstance(cpu["cpu_percent"], int)
            assert isinstance(cpu["memory_percent"], int)

        # Summary structure
        summary = data["summary"]
        assert "gpu_memory_min_mb" in summary
        assert "gpu_memory_max_mb" in summary
        assert "gpu_memory_avg_mb" in summary
        assert "gpu_util_min" in summary
        assert "gpu_util_max" in summary
        assert "gpu_util_avg" in summary
        assert "cpu_avg_percent" in summary
        for key in summary:
            assert isinstance(summary[key], int)

    @pytest.mark.asyncio
    async def test_get_node_metrics_cpu_only_node(self, auth_client):
        """Test metrics for node without GPUs (node-delta)."""
        response = await auth_client.get("/api/v1/nodes/node-delta/metrics")

        # node-delta has no GPUs, but metrics should still be generated
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "node:node-delta"
        assert "gpu_metrics" in data
        assert "cpu_metrics" in data
        assert "summary" in data
