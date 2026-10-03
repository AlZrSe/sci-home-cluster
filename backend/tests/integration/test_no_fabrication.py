"""
Integration tests for the read-time fabrication gate (issue #35).

Same shape as test_production_empty.py - the template issue #27
established - except that this one drives the real ASGI app with
SEED_DEMO_DATA off and asserts against BOTH the HTTP response and the raw
tables.

The raw-table assertions are the point. "The endpoint returned 200" is
not what regressed; a row appearing in log_entries after somebody read an
endpoint is. A test that only checked the response body would have passed
while the database filled with invented data.
"""

import pytest
from sqlalchemy import func, select

from backend.core.database import get_session
from backend.store import get_store
from backend.store.database import (
    CPUMetricModel,
    GPUMetricModel,
    LogEntryModel,
)
from backend.tests.factories import create_job_spec, create_node_spec

# Every field of JobMetricsSummary, all of which are required integers.
ZERO_SUMMARY = {
    "gpu_memory_min_mb": 0,
    "gpu_memory_max_mb": 0,
    "gpu_memory_avg_mb": 0,
    "gpu_util_min": 0,
    "gpu_util_max": 0,
    "gpu_util_avg": 0,
    "cpu_avg_percent": 0,
}

UNKNOWN_JOB = "does-not-exist"
UNKNOWN_NODE = "does-not-exist"


async def metric_row_counts() -> tuple[int, int, int]:
    """Direct (gpu_metrics, cpu_metrics, log_entries) counts, no API."""
    async with get_session() as session:
        gpu = await session.execute(select(func.count()).select_from(GPUMetricModel))
        cpu = await session.execute(select(func.count()).select_from(CPUMetricModel))
        logs = await session.execute(select(func.count()).select_from(LogEntryModel))
        return gpu.scalar_one(), cpu.scalar_one(), logs.scalar_one()


async def create_job_via_api(client, name: str) -> str:
    response = await client.post(
        "/api/v1/jobs/",
        json=create_job_spec(name=name).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    job_id: str = response.json()["job_id"]
    return job_id


async def create_node_in_store(node_id: str) -> str:
    """
    Register a node.

    There is no POST /nodes endpoint (nodes register themselves), so this
    goes through the store the way an agent would. seeding_disabled has
    already emptied the tables and turned the flag off, so nothing is
    re-seeded behind us.
    """
    store = get_store()
    await store.create_node(create_node_spec(node_id=node_id))
    return node_id


# ---------------------------------------------------------------------------
# I1 / I2 / AC-4 / AC-5: an existing resource with nothing stored
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEmptySeriesOverHttp:
    """AC-4 / AC-5: exists-but-nothing-collected is a 200 with an empty body."""

    @pytest.mark.asyncio
    async def test_metrics_for_existing_job_are_empty_when_flag_off(
        self, auth_client, seeding_disabled
    ):
        """
        I1: the body is exactly the documented empty series, and the
        tables are still empty afterwards.

        The two halves matter separately. The body pins the client-facing
        contract; the counts prove the answer was not manufactured.
        """
        job_id = await create_job_via_api(auth_client, "empty-series")
        assert await metric_row_counts() == (0, 0, 0)

        response = await auth_client.get(f"/api/v1/jobs/{job_id}/metrics")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": job_id,
            "gpu_metrics": [],
            "cpu_metrics": [],
            "summary": ZERO_SUMMARY,
        }
        assert await metric_row_counts() == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_logs_for_existing_job_are_empty_when_flag_off(
        self, auth_client, seeding_disabled
    ):
        """I2: [] rather than 404, because the job exists."""
        job_id = await create_job_via_api(auth_client, "empty-logs")

        for path in (
            f"/api/v1/jobs/{job_id}/logs",
            f"/api/v1/jobs/{job_id}/logs/history",
        ):
            response = await auth_client.get(path)
            assert response.status_code == 200, path
            assert response.json() == [], path

        assert await metric_row_counts() == (0, 0, 0)


# ---------------------------------------------------------------------------
# I3 / I4 / I5 / AC-1 / AC-2 / AC-3: unknown ids
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUnknownResourcesAre404:
    """AC-1 / AC-2 / AC-3, and the exact error codes of AC-14."""

    @pytest.mark.asyncio
    async def test_unknown_job_metrics_is_404_metrics_not_found(
        self, auth_client, seeding_disabled
    ):
        """I3: METRICS_NOT_FOUND, and nothing generated to get there."""
        response = await auth_client.get(f"/api/v1/jobs/{UNKNOWN_JOB}/metrics")

        assert response.status_code == 404
        body = response.json()
        assert body["status"] == 404
        assert body["title"] == "Not Found"
        assert body["error_code"] == "METRICS_NOT_FOUND"
        assert await metric_row_counts() == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_unknown_job_logs_and_history_are_404_logs_not_found(
        self, auth_client, seeding_disabled
    ):
        """
        I4: LOGS_NOT_FOUND on both paths, and no line of output is
        generated, stored, or returned.

        This is the headline of the issue: a typo in a job id used to
        produce a convincing 64-line log file.
        """
        for path in (
            f"/api/v1/jobs/{UNKNOWN_JOB}/logs",
            f"/api/v1/jobs/{UNKNOWN_JOB}/logs/history",
        ):
            response = await auth_client.get(path)

            assert response.status_code == 404, path
            assert response.json()["error_code"] == "LOGS_NOT_FOUND", path

        assert await metric_row_counts() == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_unknown_job_logs_is_404_even_with_flag_on(self, auth_client):
        """
        I5 / AC-3: with demo data ON an unknown job is still a 404.

        This is the one deliberate deviation from main. Gating the
        existence check on SEED_DEMO_DATA would be a regression, so it is
        asserted explicitly: the demo dataset must not resurrect the
        "any id has logs" behaviour.
        """
        assert auth_client  # the session default is SEED_DEMO_DATA=True

        expected = {
            f"/api/v1/jobs/{UNKNOWN_JOB}/logs": "LOGS_NOT_FOUND",
            f"/api/v1/jobs/{UNKNOWN_JOB}/logs/history": "LOGS_NOT_FOUND",
            f"/api/v1/jobs/{UNKNOWN_JOB}/metrics": "METRICS_NOT_FOUND",
        }
        for path, code in expected.items():
            response = await auth_client.get(path)
            assert response.status_code == 404, path
            assert response.json()["error_code"] == code, path


# ---------------------------------------------------------------------------
# I6 / AC-6: nodes
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNodeMetricsOverHttp:
    """AC-6: a quiet node is empty, a nonexistent node is 404."""

    @pytest.mark.asyncio
    async def test_node_metrics_empty_series_and_unknown_node_404(
        self, auth_client, seeding_disabled
    ):
        """I6: both halves, and the counts are untouched by either."""
        await create_node_in_store("quiet-node")

        response = await auth_client.get("/api/v1/nodes/quiet-node/metrics")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": "node:quiet-node",
            "gpu_metrics": [],
            "cpu_metrics": [],
            "summary": ZERO_SUMMARY,
        }

        missing = await auth_client.get(f"/api/v1/nodes/{UNKNOWN_NODE}/metrics")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "NODE_METRICS_NOT_FOUND"

        assert await metric_row_counts() == (0, 0, 0)


# ---------------------------------------------------------------------------
# I7 / AC-7: reads have no side effects, end to end
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReadsDoNotWriteOverHttp:
    """AC-7: every endpoint, twice, with the tables snapshotted around it."""

    @pytest.mark.asyncio
    async def test_reading_endpoints_does_not_change_row_counts(
        self, auth_client, seeding_disabled
    ):
        """
        I7: the class-of-bug guard.

        Both a job and a node exist, neither has any samples, and the flag
        is off. Each endpoint is hit twice so a write that only happens on
        a cache miss would still show up.
        """
        job_id = await create_job_via_api(auth_client, "side-effect-probe")
        await create_node_in_store("side-effect-node")

        paths = [
            f"/api/v1/jobs/{job_id}/metrics",
            f"/api/v1/jobs/{job_id}/logs",
            f"/api/v1/jobs/{job_id}/logs/history",
            "/api/v1/nodes/side-effect-node/metrics",
            f"/api/v1/jobs/{UNKNOWN_JOB}/metrics",
            f"/api/v1/jobs/{UNKNOWN_JOB}/logs",
            f"/api/v1/nodes/{UNKNOWN_NODE}/metrics",
        ]

        before = await metric_row_counts()
        assert before == (0, 0, 0)

        for _ in range(2):
            for path in paths:
                response = await auth_client.get(path)
                assert response.status_code in (200, 404), path

        assert await metric_row_counts() == before


# ---------------------------------------------------------------------------
# I8 / AC-13: the generated OpenAPI documents the 404s
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOpenApiDocumentsThe404s:
    """
    AC-13, asserted against the live app rather than the checked-in
    openapi.yaml, so a hand-edited snapshot cannot satisfy it and a
    later regeneration cannot silently drop the declarations.
    """

    DOCUMENTED_404_PATHS = [
        "/api/v1/jobs/{job_id}/metrics",
        "/api/v1/jobs/{job_id}/logs",
        "/api/v1/jobs/{job_id}/logs/history",
        "/api/v1/nodes/{node_id}/metrics",
    ]

    @pytest.mark.asyncio
    async def test_openapi_documents_404_for_metrics_and_logs(self, auth_client):
        response = await auth_client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        spec = response.json()

        # The 404 body must be a real component, not an inline blob.
        assert "ErrorResponse" in spec["components"]["schemas"]
        error_response = spec["components"]["schemas"]["ErrorResponse"]
        assert "error_code" in error_response["properties"]

        for path in self.DOCUMENTED_404_PATHS:
            responses = spec["paths"][path]["get"]["responses"]
            assert "404" in responses, path
            schema_ref = responses["404"]["content"]["application/json"]["schema"][
                "$ref"
            ]
            assert schema_ref.endswith("/ErrorResponse"), path
