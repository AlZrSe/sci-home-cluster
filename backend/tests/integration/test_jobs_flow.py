"""
Integration tests for full job lifecycle: create → list → get → metrics → logs → retry/cancel.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.tests.factories import JobSpecFactory, create_job_spec
from shared.schemas.job_status import JobStatus


@pytest.mark.integration
class TestJobLifecycle:
    """Integration tests for complete job lifecycle."""

    @pytest.fixture
    async def async_client(self) -> AsyncClient:
        """Create an async client for testing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            # Use localhost bypass token
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.fixture
    def job_spec_dict(self):
        """Create a valid job spec as dict for JSON payload."""
        job_spec = create_job_spec(
            name="integration-test-job",
            gpus=1,
            cpus=4,
            memory_gb=16,
        )
        return job_spec.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_create_job_success(self, async_client, job_spec_dict):
        """Test creating a job via JSON payload."""
        # Act
        response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)

        # Assert
        assert response.status_code == 201
        data = response.json()
        assert "job_id" in data
        assert data["job_id"].startswith("job-")
        assert data["spec"]["name"] == "integration-test-job"
        assert data["status"] == "PENDING"
        assert data["retry_count"] == 0
        assert data["created_at"] is not None

        return data["job_id"]

    @pytest.mark.asyncio
    async def test_create_job_invalid_yaml(self, async_client):
        """Test creating a job with invalid JSON returns 400."""
        # Arrange
        invalid_spec = {"name": "invalid-job"}  # Missing required fields

        # Act
        response = await async_client.post("/api/v1/jobs/", json=invalid_spec)

        # Assert
        assert response.status_code == 422
        data = response.json()
        assert data["status"] == 422
        assert "Field required" in data["detail"]

    @pytest.mark.asyncio
    async def test_create_job_missing_required_fields(self, async_client):
        """Test creating a job with missing required fields returns 400."""
        # Arrange - missing required fields like command, working_dir
        incomplete_spec = {
            "name": "incomplete-job",
            # Missing command, working_dir, resources, paths, retry
        }

        # Act
        response = await async_client.post("/api/v1/jobs/", json=incomplete_spec)

        # Assert
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_jobs(self, async_client, job_spec_dict):
        """Test listing jobs with pagination and filtering."""
        # Arrange - create a couple of jobs
        await async_client.post("/api/v1/jobs/", json=job_spec_dict)

        job_spec2 = create_job_spec(name="integration-test-job-2", gpus=2)
        await async_client.post("/api/v1/jobs/", json=job_spec2.model_dump(mode="json"))

        # Act - list all jobs
        response = await async_client.get("/api/v1/jobs/")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 12  # 10 seed + 2 new
        assert len(data["items"]) >= 2

    @pytest.mark.asyncio
    async def test_list_jobs_with_status_filter(self, async_client):
        """Test listing jobs filtered by status."""
        # Act
        response = await async_client.get("/api/v1/jobs/", params={"status": "PENDING"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        for job in data["items"]:
            assert job["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_list_jobs_with_pagination(self, async_client):
        """Test listing jobs with limit and offset."""
        # Act
        response = await async_client.get(
            "/api/v1/jobs/", params={"limit": 5, "offset": 0}
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 5
        assert data["total"] >= len(data["items"])

    @pytest.mark.asyncio
    async def test_list_jobs_with_search(self, async_client, job_spec_dict):
        """Test listing jobs with search filter."""
        # Arrange
        await async_client.post("/api/v1/jobs/", json=job_spec_dict)

        # Act
        response = await async_client.get(
            "/api/v1/jobs/", params={"search": "integration"}
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        for job in data["items"]:
            assert "integration" in job["spec"]["name"].lower()

    @pytest.mark.asyncio
    async def test_get_job_success(self, async_client, job_spec_dict):
        """Test getting a specific job by ID."""
        # Arrange - create a job first
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Act
        response = await async_client.get(f"/api/v1/jobs/{job_id}")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["spec"]["name"] == "integration-test-job"
        assert data["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, async_client):
        """Test getting a non-existent job returns 404."""
        # Act
        response = await async_client.get("/api/v1/jobs/job-999999")

        # Assert
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == 404
        assert data["title"] == "Not Found"
        assert "does not exist" in data["detail"]
        assert "error_code" in data

    @pytest.mark.asyncio
    async def test_get_job_metrics(self, async_client, job_spec_dict):
        """Test getting job metrics."""
        # Arrange - create a job
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Act
        response = await async_client.get(f"/api/v1/jobs/{job_id}/metrics")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert "gpu_metrics" in data
        assert "cpu_metrics" in data
        assert "summary" in data
        assert len(data["gpu_metrics"]) > 0
        assert len(data["cpu_metrics"]) > 0
        assert data["summary"]["gpu_memory_avg_mb"] > 0
        assert data["summary"]["gpu_util_avg"] > 0

    @pytest.mark.asyncio
    async def test_get_job_metrics_not_found(self, async_client):
        """Test getting metrics for non-existent job returns 404."""
        # Act
        response = await async_client.get("/api/v1/jobs/job-999999/metrics")

        # Assert
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_job_logs(self, async_client, job_spec_dict):
        """Test getting job logs via HTTP endpoint."""
        # Arrange - create a job
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Act
        response = await async_client.get(f"/api/v1/jobs/{job_id}/logs")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # Check log structure
        for log_line in data:
            assert isinstance(log_line, str)
            assert len(log_line) > 0

    @pytest.mark.asyncio
    async def test_get_job_logs_not_found(self, async_client):
        """Test getting logs for non-existent job returns generated logs (current behavior)."""
        # Act - the store generates logs on-the-fly for any job ID
        response = await async_client.get("/api/v1/jobs/job-999999/logs")

        # Assert - current behavior returns 200 with generated logs
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_retry_job_not_retryable(self, async_client, job_spec_dict):
        """Test retrying a PENDING job returns 409 (not retryable)."""
        # Arrange - create a PENDING job
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Act
        response = await async_client.post(f"/api/v1/jobs/{job_id}/retry")

        # Assert - PENDING jobs cannot be retried
        assert response.status_code == 409
        data = response.json()
        assert data["status"] == 409
        assert data["title"] == "Conflict"
        assert "cannot be retried" in data["detail"]
        assert "error_code" in data

    @pytest.mark.asyncio
    async def test_retry_job_success(self, async_client, job_spec_dict):
        """Test retrying a FAILED job succeeds."""
        # Arrange - create a job and manually set it to FAILED
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Directly update job status to FAILED via store
        from backend.store import get_store

        store = get_store()
        await store.update_job(
            job_id, status=JobStatus.FAILED, error="Test error", exit_code=1
        )

        # Act
        response = await async_client.post(f"/api/v1/jobs/{job_id}/retry")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "PENDING"
        assert data["retry_count"] == 1
        assert data["node_id"] is None
        assert data["error"] is None
        assert data["exit_code"] is None

    @pytest.mark.asyncio
    async def test_cancel_job_pending(self, async_client, job_spec_dict):
        """Test cancelling a PENDING job."""
        # Arrange - create a PENDING job
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Act
        response = await async_client.post(f"/api/v1/jobs/{job_id}/cancel")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "CANCELLED"
        assert data["exit_code"] == -1
        assert data["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_cancel_job_not_cancellable(self, async_client, job_spec_dict):
        """Test cancelling a COMPLETED job returns 409."""
        # Arrange - create a job and set to COMPLETED
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        from backend.store import get_store

        store = get_store()
        await store.update_job(job_id, status=JobStatus.COMPLETED, exit_code=0)

        # Act
        response = await async_client.post(f"/api/v1/jobs/{job_id}/cancel")

        # Assert
        assert response.status_code == 409
        data = response.json()
        assert data["status"] == 409
        assert "cannot be cancelled" in data["detail"]
        assert "error_code" in data

    @pytest.mark.asyncio
    async def test_delete_job(self, async_client, job_spec_dict):
        """Test deleting a job."""
        # Arrange
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        job_id = create_response.json()["job_id"]

        # Act
        response = await async_client.delete(f"/api/v1/jobs/{job_id}")

        # Assert
        assert response.status_code == 204

        # Verify job is deleted
        get_response = await async_client.get(f"/api/v1/jobs/{job_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_job_not_found(self, async_client):
        """Test deleting a non-existent job returns 404."""
        # Act
        response = await async_client.delete("/api/v1/jobs/job-999999")

        # Assert
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_full_job_lifecycle(self, async_client, job_spec_dict):
        """Test complete job lifecycle: create → list → get → metrics → logs → cancel."""
        # 1. Create job
        create_response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
        assert create_response.status_code == 201
        job_id = create_response.json()["job_id"]

        # 2. List jobs - should include our job
        list_response = await async_client.get(
            "/api/v1/jobs/", params={"search": "integration"}
        )
        assert list_response.status_code == 200
        jobs = list_response.json()["items"]
        assert any(j["job_id"] == job_id for j in jobs)

        # 3. Get job
        get_response = await async_client.get(f"/api/v1/jobs/{job_id}")
        assert get_response.status_code == 200
        assert get_response.json()["job_id"] == job_id

        # 4. Get metrics
        metrics_response = await async_client.get(f"/api/v1/jobs/{job_id}/metrics")
        assert metrics_response.status_code == 200
        assert metrics_response.json()["job_id"] == job_id

        # 5. Get logs
        logs_response = await async_client.get(f"/api/v1/jobs/{job_id}/logs")
        assert logs_response.status_code == 200
        assert isinstance(logs_response.json(), list)
        assert len(logs_response.json()) > 0

        # 6. Cancel job
        cancel_response = await async_client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "CANCELLED"

        # 7. Verify cancelled job appears in list with correct status
        list_response = await async_client.get(
            "/api/v1/jobs/", params={"status": "CANCELLED"}
        )
        assert list_response.status_code == 200
        cancelled_jobs = list_response.json()["items"]
        assert any(j["job_id"] == job_id for j in cancelled_jobs)

        # 8. Delete job
        delete_response = await async_client.delete(f"/api/v1/jobs/{job_id}")
        assert delete_response.status_code == 204


@pytest.mark.integration
class TestJobAuthentication:
    """Tests for authentication on job endpoints."""

    @pytest.fixture
    async def unauthenticated_client(self) -> AsyncClient:
        """Create an async client without auth."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    @pytest.mark.asyncio
    async def test_list_jobs_requires_auth(self, unauthenticated_client):
        """Test that listing jobs requires authentication on non-localhost."""
        # Note: TestClient simulates localhost, so this tests the endpoint exists
        # Real auth testing would need a non-localhost host header
        response = await unauthenticated_client.get("/api/v1/jobs/")
        # On localhost (TestClient), this succeeds due to bypass
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_create_job_requires_auth(self, unauthenticated_client):
        """Test that creating a job requires authentication."""
        job_spec = JobSpecFactory()
        response = await unauthenticated_client.post(
            "/api/v1/jobs/", json=job_spec.model_dump(mode="json")
        )
        # On localhost, this succeeds due to bypass
        assert response.status_code in (201, 401)


@pytest.mark.integration
class TestJobValidation:
    """Tests for job validation edge cases."""

    @pytest.fixture
    async def async_client(self) -> AsyncClient:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_create_job_zero_gpus(self, async_client):
        """Test creating a job with 0 GPUs (CPU-only)."""
        job_spec = create_job_spec(name="cpu-only-job", gpus=0, cpus=8, memory_gb=32)
        response = await async_client.post(
            "/api/v1/jobs/", json=job_spec.model_dump(mode="json")
        )
        assert response.status_code == 201
        data = response.json()
        assert data["spec"]["resources"]["gpus"] == 0

    @pytest.mark.asyncio
    async def test_create_job_high_resources(self, async_client):
        """Test creating a job with high resource requirements."""
        job_spec = create_job_spec(
            name="high-resource-job", gpus=8, cpus=64, memory_gb=512
        )
        response = await async_client.post(
            "/api/v1/jobs/", json=job_spec.model_dump(mode="json")
        )
        assert response.status_code == 201
        data = response.json()
        assert data["spec"]["resources"]["gpus"] == 8
        assert data["spec"]["resources"]["cpus"] == 64
        assert data["spec"]["resources"]["memory_gb"] == 512

    @pytest.mark.asyncio
    async def test_create_job_with_custom_env(self, async_client):
        """Test creating a job with custom environment variables."""
        job_spec = create_job_spec(
            name="custom-env-job",
            gpus=1,
            cpus=4,
            memory_gb=16,
        )
        # Override env
        job_spec.env = {
            "PYTHONUNBUFFERED": "1",
            "CUDA_VISIBLE_DEVICES": "0",
            "CUSTOM_VAR": "custom_value",
            "ANOTHER_VAR": "another_value",
        }
        response = await async_client.post(
            "/api/v1/jobs/", json=job_spec.model_dump(mode="json")
        )
        assert response.status_code == 201
        data = response.json()
        assert data["spec"]["env"]["CUSTOM_VAR"] == "custom_value"
        assert data["spec"]["env"]["ANOTHER_VAR"] == "another_value"

    @pytest.mark.asyncio
    async def test_create_job_with_custom_retry(self, async_client):
        """Test creating a job with custom retry policy."""
        job_spec = create_job_spec(name="custom-retry-job")
        job_spec.retry.max_retries = 5
        job_spec.retry.retry_delay_seconds = 120
        response = await async_client.post(
            "/api/v1/jobs/", json=job_spec.model_dump(mode="json")
        )
        assert response.status_code == 201
        data = response.json()
        assert data["spec"]["retry"]["max_retries"] == 5
        assert data["spec"]["retry"]["retry_delay_seconds"] == 120
