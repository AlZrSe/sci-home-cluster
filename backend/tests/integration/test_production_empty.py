"""
Integration tests for a production backend: no demo data.

These drive the real ASGI app with SEED_DEMO_DATA off, which is what a
cluster operator gets by default. Every test asserts against the raw
tables as well as the HTTP responses, so a test cannot pass just because
something else happened to seed the database.
"""

import pytest
from sqlalchemy import func, select

from backend.core.database import get_session
from backend.store import DatabaseStore
from backend.store.database import JobModel, NodeModel
from backend.tests.factories import create_job_spec
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus
from datetime import datetime


async def row_counts() -> tuple[int, int]:
    """Direct (jobs, nodes) row counts, bypassing the API entirely."""
    async with get_session() as session:
        jobs = await session.execute(select(func.count()).select_from(JobModel))
        nodes = await session.execute(select(func.count()).select_from(NodeModel))
        return jobs.scalar_one(), nodes.scalar_one()


def preloaded_store() -> DatabaseStore:
    """A store that writes a row without ever running the seeder."""
    store = DatabaseStore()
    store._seeded = True
    return store


def job_state(job_id: str) -> JobState:
    return JobState(
        job_id=job_id,
        spec=create_job_spec(name="synced-job"),
        status=JobStatus.PENDING,
        created_at=datetime.now(),
        retry_count=0,
    )


@pytest.mark.integration
class TestProductionDatabaseStartsEmpty:
    """AC-2: a fresh production database stays empty."""

    @pytest.mark.asyncio
    async def test_api_returns_empty_collections_with_flag_off(
        self, auth_client, seeding_disabled
    ):
        """
        I1: /jobs reports total 0 and /nodes reports [].

        Repeated three times: the lazy seeder runs on the first call and
        sets _seeded, so a second pass would hide a re-seeding bug.
        """
        for _ in range(3):
            jobs = await auth_client.get("/api/v1/jobs/")
            assert jobs.status_code == 200
            assert jobs.json()["total"] == 0
            assert jobs.json()["items"] == []

            nodes = await auth_client.get("/api/v1/nodes/")
            assert nodes.status_code == 200
            assert nodes.json() == []

        # Assert on the tables, not only on the API (AC-9 / R4).
        assert await row_counts() == (0, 0)

    @pytest.mark.asyncio
    async def test_create_job_on_virgin_db_gets_job_1(
        self, auth_client, seeding_disabled
    ):
        """
        I2: the first job on a genuinely empty database is job-1.

        Correct, because there is nothing to collide with. Pinned so nobody
        "fixes" the counter by pre-seeding it with a constant - that would
        reintroduce the collision in the next test.
        """
        response = await auth_client.post(
            "/api/v1/jobs/",
            json=create_job_spec(name="first-job").model_dump(mode="json"),
        )

        assert response.status_code == 201
        assert response.json()["job_id"] == "job-1"
        assert await row_counts() == (1, 0)

    @pytest.mark.asyncio
    async def test_health_endpoint_on_empty_db(self, auth_client, seeding_disabled):
        """
        I3: /health still returns 200 on an empty cluster.

        The body reports status "degraded" because main.py:255 derives
        store health from the node count and no agent has registered yet.
        That is a known, deliberately unfixed consequence of defaulting the
        seeder off - the health contract is tracked separately in #32 and
        must not be "fixed" by re-enabling seeding.

        The 200 matters: the frontend probes /health to decide whether the
        backend is reachable and falls back to mock data otherwise.
        """
        response = await auth_client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["store"]["status"] == "degraded"
        assert data["store"]["nodes"] == 0
        assert data["database"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_is_healthy_once_a_node_registers(
        self, auth_client, seeded_cluster
    ):
        """
        The counterpart to I3: one registered node is enough for "healthy".

        Confirms the degraded status above is about the node count and not
        about the database or the config being broken.
        """
        response = await auth_client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["store"]["nodes"] == 1


@pytest.mark.integration
class TestRestartSafety:
    """AC-5 / AC-6 over the real HTTP API."""

    @pytest.mark.asyncio
    async def test_restart_then_create_does_not_collide(
        self, auth_client, seeding_disabled
    ):
        """
        I4: a restart against a synced database does not reuse ids.

        Models the real sequence: Syncthing scans the shared folder and
        inserts job-1 and job-2, the backend restarts (a brand new store
        instance, so _job_counter is 0 again and _seeded is False), and the
        first request arrives. Without the counter sync the new job would
        be issued as job-1 and POST /jobs would return 500.
        """
        preloaded = preloaded_store()
        await preloaded.create_job_with_id(job_state("job-1"))
        await preloaded.create_job_with_id(job_state("job-2"))

        # The restart: a fresh singleton with no in-memory state carried over.
        restarted = DatabaseStore()
        assert restarted._job_counter == 0

        response = await auth_client.post(
            "/api/v1/jobs/",
            json=create_job_spec(name="post-restart").model_dump(mode="json"),
        )

        assert response.status_code == 201
        new_id = response.json()["job_id"]
        assert int(new_id.split("-")[1]) > 2
        # The synced rows survive untouched (AC-11).
        assert await row_counts() == (3, 0)
