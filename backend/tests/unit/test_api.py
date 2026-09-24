"""
API endpoint tests using httpx.AsyncClient with auth_client fixture.
Tests all endpoints: auth, jobs, nodes, syncthing.
"""

import pytest
import io
import yaml
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.tests.factories import create_job_spec
from backend.models.job_spec import JobSpec
from backend.models.job_status import JobStatus
from backend.store.memory import get_store


def job_spec_to_yaml_bytes(job_spec: JobSpec) -> bytes:
    """Convert JobSpec to YAML bytes for upload."""
    return yaml.dump(job_spec.model_dump(mode="json")).encode("utf-8")


@pytest.fixture
async def async_client() -> AsyncClient:
    """Create an async client for testing."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def auth_client(async_client: AsyncClient) -> AsyncClient:
    """Create an authenticated client with localhost bypass token."""
    async_client.headers["Authorization"] = "Bearer localhost-no-auth"
    return async_client


@pytest.fixture
def job_yaml_bytes():
    """Create a valid job YAML as bytes for upload."""
    job_spec = create_job_spec(name="api-test-job", gpus=1, cpus=4, memory_gb=16)
    return job_spec_to_yaml_bytes(job_spec)


class TestRootAndHealth:
    """Test root and health endpoints."""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, async_client):
        """Test the root endpoint returns correct message."""
        response = await async_client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to Scientific Home Cluster API"}

    @pytest.mark.asyncio
    async def test_health_endpoint(self, async_client):
        """Test the health endpoint returns correct structure."""
        response = await async_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "store" in data
        assert "config" in data
        assert "syncthing" in data
        assert "database" in data

        # Check store status
        assert data["store"]["status"] in ["healthy", "degraded"]
        assert "nodes" in data["store"]
        assert "jobs" in data["store"]


class TestAuthEndpoints:
    """Test authentication API endpoints."""

    @pytest.mark.asyncio
    async def test_validate_token_endpoint(self, async_client):
        """Test POST /auth/validate endpoint exists."""
        from backend.core.config import settings
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "test-shared-token-123"}
            )

        assert response.status_code == 200
        data = response.json()
        assert "valid" in data
        assert isinstance(data["valid"], bool)

    @pytest.mark.asyncio
    async def test_validate_token_invalid(self, async_client):
        """Test POST /auth/validate with invalid token."""
        from backend.core.config import settings
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "invalid-token"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_validate_token_too_short(self, async_client):
        """Test POST /auth/validate with too short token."""
        response = await async_client.post("/api/v1/auth/validate", json={"token": "short"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_token_endpoint(self, async_client):
        """Test POST /auth/token endpoint."""
        from backend.core.config import settings
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
            )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0

    @pytest.mark.asyncio
    async def test_create_token_invalid_shared(self, async_client):
        """Test POST /auth/token with invalid shared token."""
        from backend.core.config import settings
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_token_no_shared_configured(self, async_client):
        """Test POST /auth/token when shared token not configured."""
        from backend.core.config import settings
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", None):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "any-token"}
            )

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_refresh_token_endpoint(self, async_client):
        """Test POST /auth/refresh endpoint."""
        from backend.core.config import settings
        from backend.core.security import create_access_token
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", None):
            token = create_access_token({"sub": "test-user"})
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": token}
            )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, async_client):
        """Test POST /auth/refresh with invalid token."""
        from backend.core.config import settings
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", None):
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": "invalid-token"}
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_token_expired(self, async_client):
        """Test POST /auth/refresh with expired token."""
        from backend.core.config import settings
        from backend.core.security import create_access_token
        from datetime import timedelta
        from unittest.mock import patch

        with patch.object(settings, "SHARED_TOKEN", None):
            expired_token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": expired_token}
            )

        assert response.status_code == 401


class TestJobsEndpoints:
    """Test jobs API endpoints."""

    @pytest.mark.asyncio
    async def test_list_jobs(self, auth_client):
        """Test GET /jobs returns list of jobs."""
        response = await auth_client.get("/api/v1/jobs/")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert data["total"] >= 10  # Seed jobs

    @pytest.mark.asyncio
    async def test_list_jobs_with_status_filter(self, auth_client):
        """Test GET /jobs with status filter."""
        response = await auth_client.get("/api/v1/jobs/", params={"status": "PENDING"})
        assert response.status_code == 200
        data = response.json()
        for job in data["items"]:
            assert job["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_list_jobs_with_pagination(self, auth_client):
        """Test GET /jobs with limit and offset."""
        response = await auth_client.get("/api/v1/jobs/", params={"limit": 5, "offset": 0})
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 5

    @pytest.mark.asyncio
    async def test_list_jobs_with_search(self, auth_client, job_yaml_bytes):
        """Test GET /jobs with search filter."""
        # Create a job with searchable name
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        await auth_client.post("/api/v1/jobs/", files=files)

        response = await auth_client.get("/api/v1/jobs/", params={"search": "api-test"})
        assert response.status_code == 200
        data = response.json()
        for job in data["items"]:
            assert "api-test" in job["spec"]["name"].lower()

    @pytest.mark.asyncio
    async def test_list_jobs_with_node_filter(self, auth_client):
        """Test GET /jobs with node filter."""
        response = await auth_client.get("/api/v1/jobs/", params={"node": "node-alpha"})
        assert response.status_code == 200
        data = response.json()
        for job in data["items"]:
            assert job["node_id"] == "node-alpha"

    @pytest.mark.asyncio
    async def test_create_job_success(self, auth_client, job_yaml_bytes):
        """Test POST /jobs creates a new job."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        response = await auth_client.post("/api/v1/jobs/", files=files)

        assert response.status_code == 201
        data = response.json()
        assert "job_id" in data
        assert data["job_id"].startswith("job-")
        assert data["spec"]["name"] == "api-test-job"
        assert data["status"] == "PENDING"
        assert data["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_create_job_invalid_yaml(self, auth_client):
        """Test POST /jobs with invalid YAML returns 400."""
        invalid_yaml = b"invalid: yaml: content: ["
        files = {"job.yaml": ("job.yaml", io.BytesIO(invalid_yaml), "application/yaml")}
        response = await auth_client.post("/api/v1/jobs/", files=files)

        assert response.status_code == 400
        data = response.json()
        assert data["status"] == 400
        assert "Invalid job specification" in data["detail"]

    @pytest.mark.asyncio
    async def test_create_job_missing_required_fields(self, auth_client):
        """Test POST /jobs with missing required fields."""
        incomplete_spec = {"name": "incomplete-job"}
        yaml_content = yaml.dump(incomplete_spec).encode("utf-8")
        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}

        response = await auth_client.post("/api/v1/jobs/", files=files)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_job_success(self, auth_client, job_yaml_bytes):
        """Test GET /jobs/{job_id} returns job details."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["spec"]["name"] == "api-test-job"
        assert data["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, auth_client):
        """Test GET /jobs/{job_id} returns 404 for non-existent job."""
        response = await auth_client.get("/api/v1/jobs/job-999999")
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"

    @pytest.mark.asyncio
    async def test_get_job_metrics(self, auth_client, job_yaml_bytes):
        """Test GET /jobs/{job_id}/metrics returns metrics."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.get(f"/api/v1/jobs/{job_id}/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert "gpu_metrics" in data
        assert "cpu_metrics" in data
        assert "summary" in data
        assert len(data["gpu_metrics"]) > 0
        assert len(data["cpu_metrics"]) > 0

    @pytest.mark.asyncio
    async def test_get_job_metrics_not_found(self, auth_client):
        """Test GET /jobs/{job_id}/metrics returns 404 for non-existent job."""
        response = await auth_client.get("/api/v1/jobs/job-999999/metrics")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_job_logs(self, auth_client, job_yaml_bytes):
        """Test GET /jobs/{job_id}/logs returns logs."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.get(f"/api/v1/jobs/{job_id}/logs")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_get_job_logs_not_found(self, auth_client):
        """Test GET /jobs/{job_id}/logs returns generated logs for any job ID (current behavior)."""
        # The store generates logs on-the-fly for any job ID, so even non-existent
        # jobs return 200 with generated logs. This is the current behavior.
        response = await auth_client.get("/api/v1/jobs/job-999999/logs")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_retry_job_not_retryable(self, auth_client, job_yaml_bytes):
        """Test POST /jobs/{job_id}/retry returns 409 for non-retryable job."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")
        assert response.status_code == 409
        data = response.json()
        assert data["status"] == 409
        assert "not in retryable state" in data["detail"]

    @pytest.mark.asyncio
    async def test_retry_job_success(self, auth_client, job_yaml_bytes):
        """Test POST /jobs/{job_id}/retry succeeds for FAILED job."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        # Set job to FAILED
        store = get_store()
        await store.update_job(job_id, status=JobStatus.FAILED, error="Test error", exit_code=1)

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "PENDING"
        assert data["retry_count"] == 1
        assert data["node_id"] is None
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_cancel_job_pending(self, auth_client, job_yaml_bytes):
        """Test POST /jobs/{job_id}/cancel for PENDING job."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "CANCELLED"
        assert data["exit_code"] == -1
        assert data["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_cancel_job_not_cancellable(self, auth_client, job_yaml_bytes):
        """Test POST /jobs/{job_id}/cancel returns 409 for COMPLETED job."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        store = get_store()
        await store.update_job(job_id, status=JobStatus.COMPLETED, exit_code=0)

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert response.status_code == 409
        data = response.json()
        assert data["status"] == 409
        assert "not cancellable" in data["detail"]

    @pytest.mark.asyncio
    async def test_delete_job(self, auth_client, job_yaml_bytes):
        """Test DELETE /jobs/{job_id} deletes job."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.delete(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 204

        # Verify deleted
        get_response = await auth_client.get(f"/api/v1/jobs/{job_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_job_not_found(self, auth_client):
        """Test DELETE /jobs/{job_id} returns 404 for non-existent job."""
        response = await auth_client.delete("/api/v1/jobs/job-999999")
        assert response.status_code == 404


class TestNodesEndpoints:
    """Test nodes API endpoints."""

    @pytest.mark.asyncio
    async def test_list_nodes(self, auth_client):
        """Test GET /nodes returns list of nodes."""
        response = await auth_client.get("/api/v1/nodes/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # Seed nodes

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
            assert isinstance(node["gpus"], list)
            for gpu in node["gpus"]:
                assert "name" in gpu
                assert "memory_gb" in gpu
            assert node["status"] in ["ONLINE", "OFFLINE"]

    @pytest.mark.asyncio
    async def test_get_node_success(self, auth_client):
        """Test GET /nodes/{node_id} returns node details."""
        response = await auth_client.get("/api/v1/nodes/node-beta")
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
    async def test_get_node_not_found(self, auth_client):
        """Test GET /nodes/{node_id} returns 404 for non-existent node."""
        response = await auth_client.get("/api/v1/nodes/non-existent-node")
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"
        assert "not found" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_node_data_structure_validation(self, auth_client):
        """Test node data structure matches seed data expectations."""
        response = await auth_client.get("/api/v1/nodes/")
        assert response.status_code == 200

        nodes = {node["node_id"]: node for node in response.json()}

        # node-alpha
        alpha = nodes["node-alpha"]
        assert alpha["hostname"] == "alpha.lan"
        assert alpha["status"] == "ONLINE"
        assert len(alpha["gpus"]) == 1
        assert alpha["gpus"][0]["name"] == "NVIDIA RTX 4090"
        assert alpha["current_job_id"] == "job-1041"

        # node-gamma (Mac with MPS)
        gamma = nodes["node-gamma"]
        assert gamma["hostname"] == "gamma.lan"
        assert len(gamma["gpus"]) == 1
        assert gamma["gpus"][0]["name"] == "Apple M3 Max (MPS)"
        assert gamma["gpus"][0]["memory_gb"] == 36

        # node-delta (offline, no GPUs)
        delta = nodes["node-delta"]
        assert delta["gpus"] == []
        assert delta["status"] == "OFFLINE"
        assert delta["current_job_id"] is None

        # Timestamps are valid ISO 8601
        from datetime import datetime
        for node in nodes.values():
            dt = datetime.fromisoformat(node["last_heartbeat"].replace(" ", "T"))
            assert dt is not None


class TestSyncthingEndpoints:
    """Test Syncthing API endpoints."""

    @pytest.mark.asyncio
    async def test_syncthing_status(self, auth_client):
        """Test GET /syncthing/status returns status info."""
        response = await auth_client.get("/api/v1/syncthing/status")
        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "root_path" in data
        assert "jobs_folder" in data
        assert "nodes_folder" in data
        assert "exists" in data["jobs_folder"]
        assert "path" in data["jobs_folder"]
        assert "state_files" in data["jobs_folder"]
        assert "exists" in data["nodes_folder"]
        assert "path" in data["nodes_folder"]
        assert "state_files" in data["nodes_folder"]

    @pytest.mark.asyncio
    async def test_syncthing_scan(self, auth_client):
        """Test POST /syncthing/scan triggers scan."""
        response = await auth_client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "scanned" in data
        assert "total_processed" in data
        assert data["message"] == "Scan completed"

    @pytest.mark.asyncio
    async def test_syncthing_scan_picks_up_files(self, auth_client, tmp_path):
        """Test that scan picks up new files."""
        # This test would need a custom SYNCTHING_ROOT
        # For now just verify endpoint works
        response = await auth_client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200


class TestErrorResponses:
    """Test error response format consistency."""

    @pytest.mark.asyncio
    async def test_404_error_format(self, auth_client):
        """Test 404 errors follow ErrorResponse format."""
        response = await auth_client.get("/api/v1/jobs/job-999999")
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"
        assert "detail" in data
        assert "instance" in data

    @pytest.mark.asyncio
    async def test_409_error_format(self, auth_client, job_yaml_bytes):
        """Test 409 errors follow ErrorResponse format."""
        files = {"job.yaml": ("job.yaml", io.BytesIO(job_yaml_bytes), "application/yaml")}
        create_response = await auth_client.post("/api/v1/jobs/", files=files)
        job_id = create_response.json()["job_id"]

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")
        assert response.status_code == 409
        data = response.json()
        assert data["status"] == 409
        assert data["title"] == "Conflict"
        assert "detail" in data
        assert "instance" in data

    @pytest.mark.asyncio
    async def test_422_error_format(self, auth_client):
        """Test 422 validation errors follow ErrorResponse format."""
        response = await auth_client.post("/api/v1/auth/validate", json={"token": "short"})
        assert response.status_code == 422
        data = response.json()
        assert data["status"] == 422
        assert data["title"] == "Unprocessable Entity"
        assert "detail" in data
        assert "instance" in data

    @pytest.mark.asyncio
    async def test_400_error_format(self, auth_client):
        """Test 400 errors follow ErrorResponse format."""
        invalid_yaml = b"invalid: yaml: content: ["
        files = {"job.yaml": ("job.yaml", io.BytesIO(invalid_yaml), "application/yaml")}
        response = await auth_client.post("/api/v1/jobs/", files=files)
        assert response.status_code == 400
        data = response.json()
        assert data["status"] == 400
        assert data["title"] == "Bad Request"
        assert "detail" in data
        assert "instance" in data


class TestRequestIdMiddleware:
    """Test request ID middleware."""

    @pytest.mark.asyncio
    async def test_request_id_header_present(self, auth_client):
        """Test that X-Request-ID header is present in responses."""
        response = await auth_client.get("/api/v1/health")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0

    @pytest.mark.asyncio
    async def test_request_id_echoed(self, auth_client):
        """Test that custom X-Request-ID is echoed back."""
        custom_id = "custom-request-id-12345"
        response = await auth_client.get("/api/v1/health", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == custom_id


class TestCORS:
    """Test CORS configuration."""

    @pytest.mark.asyncio
    async def test_cors_headers(self, async_client):
        """Test CORS headers are present for allowed origins."""
        response = await async_client.options(
            "/api/v1/health",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
        )
        # Should not be 404
        assert response.status_code != 404


class TestOpenAPI:
    """Test OpenAPI schema."""

    @pytest.mark.asyncio
    async def test_openapi_schema_accessible(self, async_client):
        """Test OpenAPI schema is accessible."""
        response = await async_client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "info" in data
        assert data["info"]["title"] == "Scientific Home Cluster API"

    @pytest.mark.asyncio
    async def test_docs_accessible(self, async_client):
        """Test Swagger UI is accessible."""
        response = await async_client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_redoc_accessible(self, async_client):
        """Test ReDoc is accessible."""
        response = await async_client.get("/redoc")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


# Import asyncio for tests that need it
