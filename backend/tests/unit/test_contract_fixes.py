"""
Tests for the API-to-frontend contract fixes in issue #20.

The frontend is a separate repository and talks HTTP/JSON only, so every
fix here is backend-side.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import inspect as sa_inspect

from backend.core.config import settings
from backend.core.database import get_engine
from backend.core.security import create_access_token
from backend.main import app
from backend.store.database import CPUMetricModel


@pytest.fixture
async def async_client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        client.headers["Authorization"] = "Bearer localhost-no-auth"
        yield client


class TestNodeFilterAlias:
    """
    The web client sends `node_id`; the route declared `node`. FastAPI
    ignores unknown query parameters, so node filtering silently returned
    everything.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("param", ["node", "node_id"])
    async def test_both_parameter_names_filter(self, async_client, param):
        response = await async_client.get(f"/api/v1/jobs/?{param}=node-alpha")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] > 0, "the seed data has jobs on node-alpha"
        assert {job["node_id"] for job in data["items"]} == {"node-alpha"}

    @pytest.mark.asyncio
    async def test_unknown_node_filters_to_empty(self, async_client):
        response = await async_client.get("/api/v1/jobs/?node_id=node-does-not-exist")
        assert response.status_code == 200
        assert response.json()["total"] == 0


class TestCpuMetricFields:
    """temperature_c and memory_used_gb are required by the client."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/api/v1/jobs/job-1050/metrics"])
    async def test_job_metrics_include_the_new_fields(self, async_client, path):
        response = await async_client.get(path)
        assert response.status_code == 200

        cpu_metrics = response.json()["cpu_metrics"]
        assert cpu_metrics, "seeded jobs expose CPU samples"
        for sample in cpu_metrics:
            assert isinstance(sample["temperature_c"], int)
            assert isinstance(sample["memory_used_gb"], float)
            assert sample["memory_used_gb"] >= 0

    @pytest.mark.asyncio
    async def test_node_metrics_include_the_new_fields(self, async_client):
        response = await async_client.get("/api/v1/nodes/node-alpha/metrics")
        assert response.status_code == 200

        cpu_metrics = response.json()["cpu_metrics"]
        assert cpu_metrics
        for sample in cpu_metrics:
            assert "temperature_c" in sample
            assert "memory_used_gb" in sample

    def test_the_columns_exist_in_the_schema(self):
        """The migration must have added the columns to the model."""
        assert "temperature_c" in CPUMetricModel.__table__.columns
        assert "memory_used_gb" in CPUMetricModel.__table__.columns

    def test_columns_are_not_nullable(self):
        assert CPUMetricModel.__table__.columns["temperature_c"].nullable is False
        assert CPUMetricModel.__table__.columns["memory_used_gb"].nullable is False

    @pytest.mark.asyncio
    async def test_migration_is_applied_to_a_live_database(self):
        engine = get_engine()

        def has_columns(connection):
            inspector = sa_inspect(connection)
            if "cpu_metrics" not in inspector.get_table_names():
                return None
            return {
                "temperature_c",
                "memory_used_gb",
            } <= {c["name"] for c in inspector.get_columns("cpu_metrics")}

        async with engine.connect() as conn:
            result = await conn.run_sync(has_columns)

        if result is None:
            pytest.skip("cpu_metrics table not present in this test database")
        assert result, "the cpu_metrics table is missing the migrated columns"


class TestWebSocketSubprotocolAuth:
    """
    A browser WebSocket can set neither headers nor query parameters, so a
    client could never authenticate a log stream over a non-local host.
    """

    def test_token_is_read_from_the_subprotocol(self):
        from backend.core.deps import _token_from_subprotocol

        class FakeWebSocket:
            def __init__(self, header):
                self.headers = {"sec-websocket-protocol": header} if header else {}

        assert _token_from_subprotocol(FakeWebSocket("bearer, abc.def.ghi")) == (
            "abc.def.ghi"
        )
        # Without the "bearer" marker the handshake is not ours to accept.
        assert _token_from_subprotocol(FakeWebSocket("abc.def.ghi")) is None
        assert _token_from_subprotocol(FakeWebSocket("")) is None
        assert _token_from_subprotocol(FakeWebSocket(None)) is None

    def test_accepted_subprotocol_is_echoed(self):
        from backend.core.deps import ws_accepted_subprotocol

        class FakeWebSocket:
            def __init__(self, header):
                self.headers = {"sec-websocket-protocol": header} if header else {}

        assert ws_accepted_subprotocol(FakeWebSocket("bearer, token")) == "bearer"
        assert ws_accepted_subprotocol(FakeWebSocket("Bearer, token")) == "bearer"
        assert ws_accepted_subprotocol(FakeWebSocket("something-else")) is None
        assert ws_accepted_subprotocol(FakeWebSocket(None)) is None

    def test_issued_token_is_usable_as_a_subprotocol(self):
        token = create_access_token({"sub": "tester"})
        assert token.count(".") == 2
