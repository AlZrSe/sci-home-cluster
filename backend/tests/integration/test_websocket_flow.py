"""
Integration tests for job logs endpoints (HTTP fallback and history).
"""

import pytest
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from backend.main import app


@pytest.mark.integration
class TestJobLogsHistory:
    """Integration tests for GET /jobs/{id}/logs/history endpoint."""

    @pytest.fixture
    async def auth_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client with localhost bypass auth."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_logs_history_endpoint(self, auth_client):
        """Test GET /jobs/{id}/logs/history returns logs for existing job."""
        # Use a known seed job
        response = await auth_client.get("/api/v1/jobs/job-1041/logs/history")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        for log_line in data:
            assert isinstance(log_line, str)
            assert len(log_line) > 0

    @pytest.mark.asyncio
    async def test_logs_history_not_found(self, auth_client):
        """Test GET /jobs/{id}/logs/history returns 404 for a non-existent job."""
        # Used to return 200 with 64 generated lines: the store generated
        # logs on-the-fly for any job id at all (issue #35).
        response = await auth_client.get("/api/v1/jobs/job-999999/logs/history")

        assert response.status_code == 404
        assert response.json()["error_code"] == "LOGS_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_logs_history_equals_logs_endpoint(self, auth_client):
        """Test /logs/history returns same data as /logs endpoint."""
        job_id = "job-1041"

        history_response = await auth_client.get(f"/api/v1/jobs/{job_id}/logs/history")
        logs_response = await auth_client.get(f"/api/v1/jobs/{job_id}/logs")

        assert history_response.status_code == 200
        assert logs_response.status_code == 200
        assert history_response.json() == logs_response.json()


@pytest.mark.integration
class TestJobLogsHTTP:
    """Integration tests for GET /jobs/{id}/logs HTTP fallback endpoint."""

    @pytest.fixture
    async def auth_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client with localhost bypass auth."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_get_job_logs_success(self, auth_client):
        """Test getting job logs via HTTP endpoint."""
        job_id = "job-1041"
        response = await auth_client.get(f"/api/v1/jobs/{job_id}/logs")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_get_job_logs_structure(self, auth_client):
        """Test log structure matches expected format."""
        job_id = "job-1041"
        response = await auth_client.get(f"/api/v1/jobs/{job_id}/logs")

        assert response.status_code == 200
        data = response.json()

        # Check first few lines have expected format
        first_line = data[0]
        assert (
            "INFO" in first_line
            or "DEBUG" in first_line
            or "WARN" in first_line
            or "ERROR" in first_line
        )
        assert "job-" in first_line

    @pytest.mark.asyncio
    async def test_get_job_logs_is_not_generated_for_an_unknown_id(self, auth_client):
        """No log lines are invented for an id that has no job behind it.

        This test existed only to pin the bug: it asserted 200 with a
        non-empty body for `job-does-not-exist`. It is inverted now
        (issue #35) rather than deleted, so the 404 stays pinned at the
        HTTP layer on both the /logs and the /logs/history path.
        """
        response = await auth_client.get("/api/v1/jobs/job-does-not-exist/logs")

        assert response.status_code == 404
        assert response.json()["error_code"] == "LOGS_NOT_FOUND"
