"""
Unit tests for Syncthing service.
"""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.services.syncthing_service import SyncthingService
from shared.schemas.job_state import JobState
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec, GPUInfo
from backend.store import get_store
from backend.core.config import settings


@pytest.fixture
def temp_syncthing_root():
    """Create a temporary Syncthing root directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "jobs").mkdir()
        (root / "nodes").mkdir()
        yield root


@pytest.fixture
async def syncthing_service(temp_syncthing_root):
    """
    Create a SyncthingService instance with a temp directory.

    Deliberately an async fixture: the teardown must await stop() on the
    SAME event loop the service was started on. A sync fixture calling
    asyncio.get_event_loop() gets a closed or foreign loop, the RuntimeError
    is swallowed, and the watchdog thread is left running - which then
    writes to the database concurrently with later tests and makes them
    fail with "database is locked".
    """
    service = SyncthingService(temp_syncthing_root)
    # Don't start here - let tests start it if needed
    yield service
    # Cleanup - stop if running
    if service._running:
        await service.stop()


@pytest.fixture(autouse=True)
async def reset_store_fixture():
    """Reset store before each test."""
    from backend.store import get_store

    store = get_store()
    await store.reset()
    yield
    await store.reset()


class TestSyncthingService:
    """Tests for SyncthingService."""

    @pytest.mark.asyncio
    async def test_service_start_stop(self, temp_syncthing_root):
        """Test service can start and stop."""
        service = SyncthingService(temp_syncthing_root)
        assert not service._running

        await service.start()
        assert service._running
        assert service.observer is not None
        assert service.observer.is_alive()

        await service.stop()
        assert not service._running

    @pytest.mark.asyncio
    async def test_initial_scan_jobs(self, syncthing_service, temp_syncthing_root):
        """Test initial scan picks up job state files."""
        # Start the service
        await syncthing_service.start()

        # Create a job state file
        job_dir = temp_syncthing_root / "jobs" / "job-1001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="test-job",
            command="python train.py",
            working_dir="/workspace",
            env={},
            resources={"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
            paths={"input": "/data/in", "output": "/data/out"},
            retry={"max_retries": 3, "retry_delay_seconds": 60},
        )

        job_state = JobState(
            job_id="job-1001",
            spec=job_spec,
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            retry_count=0,
        )

        # Write YAML manually for testing
        import yaml

        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        # Trigger manual scan
        result = await syncthing_service.manual_scan()

        assert result["scanned"] >= 1

        # Verify job was loaded into store
        store = get_store()
        job = await store.get_job("job-1001")
        assert job is not None
        assert job.job_id == "job-1001"
        assert job.spec.name == "test-job"

    @pytest.mark.asyncio
    async def test_initial_scan_nodes(self, syncthing_service, temp_syncthing_root):
        """Test initial scan picks up node state files."""
        # Start the service
        await syncthing_service.start()

        # Create a node state file
        node_file = temp_syncthing_root / "nodes" / "node-test.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-test",
            hostname="test.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )

        import yaml

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Trigger manual scan
        result = await syncthing_service.manual_scan()

        assert result["scanned"] >= 1

        # Verify node was loaded into store
        store = get_store()
        node = await store.get_node("node-test")
        assert node is not None
        assert node.node_id == "node-test"
        assert node.hostname == "test.lan"

    @pytest.mark.asyncio
    async def test_get_status(self, syncthing_service, temp_syncthing_root):
        """Test get_status returns correct info."""
        await syncthing_service.start()
        status = syncthing_service.get_status()

        assert status["running"] is True
        assert status["root_path"] == str(temp_syncthing_root)
        assert status["jobs_dir_exists"] is True
        assert status["nodes_dir_exists"] is True

    @pytest.mark.asyncio
    async def test_manual_scan_empty(self, syncthing_service):
        """Test manual scan on empty directories."""
        await syncthing_service.start()
        result = await syncthing_service.manual_scan()
        assert result["scanned"] == 0
        assert "total_processed" in result

    @pytest.mark.asyncio
    async def test_invalid_job_id_ignored(self, syncthing_service, temp_syncthing_root):
        """Test that invalid job IDs are ignored."""
        await syncthing_service.start()
        # Create a job with invalid ID (create file directly,
        # bypassing JobState validation)
        job_dir = temp_syncthing_root / "jobs" / "invalid-job"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        import yaml

        # Write invalid job ID directly to YAML
        with open(state_file, "w") as f:
            yaml.dump(
                {
                    "job_id": "invalid-job",
                    "spec": {
                        "name": "test-job",
                        "command": "python train.py",
                        "working_dir": "/workspace",
                        "env": {},
                        "resources": {
                            "gpus": 1,
                            "cpus": 4,
                            "memory_gb": 16,
                            "vram_gb": 8,
                        },
                        "paths": {"input": "/data/in", "output": "/data/out"},
                        "retry": {"max_retries": 3, "retry_delay_seconds": 60},
                    },
                    "status": "PENDING",
                    "created_at": datetime.now().isoformat(),
                    "retry_count": 0,
                },
                f,
            )

        await syncthing_service.manual_scan()
        # Should not process invalid job ID
        store = get_store()
        job = await store.get_job("invalid-job")
        assert job is None


class TestSyncthingAPI:
    """Tests for Syncthing API endpoints - skipped.
    Reason: missing app.state.syncthing_service in test environment.
    """

    @pytest.mark.skip(
        reason=(
            "Requires app.state.syncthing_service which is set by "
            "lifespan (not run in unit tests)"
        )
    )
    def test_syncthing_status_endpoint(self, client):
        """Test GET /api/v1/syncthing/status"""
        response = client.get("/api/v1/syncthing/status")
        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "root_path" in data
        assert "jobs_folder" in data
        assert "nodes_folder" in data

    @pytest.mark.skip(
        reason=(
            "Requires app.state.syncthing_service which is set by "
            "lifespan (not run in unit tests)"
        )
    )
    def test_syncthing_scan_endpoint(self, client):
        """Test POST /api/v1/syncthing/scan"""
        response = client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "scanned" in data
        assert "total_processed" in data


class TestHeartbeatMonitor:
    """Tests for the heartbeat monitor (issue #96)."""

    @pytest.fixture
    def fake_store(self):
        """Create a fake store for testing the monitor in isolation."""

        class FakeStore:
            def __init__(self):
                self.nodes: dict[str, NodeSpec] = {}
                self.update_calls: list[tuple[str, dict]] = []

            async def list_nodes(self) -> list[NodeSpec]:
                return list(self.nodes.values())

            async def update_node(self, node_id: str, **kwargs) -> NodeSpec | None:
                if node_id not in self.nodes:
                    return None
                self.update_calls.append((node_id, kwargs))
                current = self.nodes[node_id]
                self.nodes[node_id] = current.model_copy(update=kwargs)
                return self.nodes[node_id]

            async def create_node(self, node_spec: NodeSpec) -> NodeSpec:
                if node_spec.node_id in self.nodes:
                    raise ValueError(f"Node with ID {node_spec.node_id} already exists")
                self.nodes[node_spec.node_id] = node_spec
                return node_spec

            async def get_node(self, node_id: str) -> NodeSpec | None:
                return self.nodes.get(node_id)

        store = FakeStore()
        # Patch get_store to return our fake store
        with patch("backend.services.syncthing_service.get_store", return_value=store):
            yield store

    def _make_node(
        self,
        node_id: str,
        last_heartbeat: datetime,
        status: str = "ONLINE",
    ) -> NodeSpec:
        """Create a NodeSpec with the given heartbeat and status."""
        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        return NodeSpec(
            node_id=node_id,
            hostname="test.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status=status,
            last_heartbeat=last_heartbeat,
            current_job_id=None,
        )

    @pytest.mark.asyncio
    async def test_stale_node_marked_offline(self, fake_store):
        """Stale → OFFLINE: heartbeat 120s old → status becomes OFFLINE."""
        now = datetime.now(timezone.utc)
        stale_hb = now - timedelta(seconds=120)
        node = self._make_node("node-stale", stale_hb, status="ONLINE")
        await fake_store.create_node(node)

        service = SyncthingService(Path("/tmp/fake"))
        await service._heartbeat_monitor_iteration(now=now)

        assert fake_store.nodes["node-stale"].status == "OFFLINE"
        assert len(fake_store.update_calls) == 1
        assert fake_store.update_calls[0][1]["status"] == "OFFLINE"

    @pytest.mark.asyncio
    async def test_fresh_node_marked_online(self, fake_store):
        """Fresh → ONLINE: heartbeat 10s old, status OFFLINE → becomes ONLINE."""
        now = datetime.now(timezone.utc)
        fresh_hb = now - timedelta(seconds=10)
        node = self._make_node("node-fresh", fresh_hb, status="OFFLINE")
        await fake_store.create_node(node)

        service = SyncthingService(Path("/tmp/fake"))
        await service._heartbeat_monitor_iteration(now=now)

        assert fake_store.nodes["node-fresh"].status == "ONLINE"
        assert len(fake_store.update_calls) == 1
        assert fake_store.update_calls[0][1]["status"] == "ONLINE"

    @pytest.mark.asyncio
    async def test_boundary_delta_equals_timeout_stays_online(self, fake_store):
        """Boundary delta == timeout → stays ONLINE (condition is delta > timeout)."""
        now = datetime.now(timezone.utc)
        boundary_hb = now - timedelta(seconds=settings.HEARTBEAT_TIMEOUT_SECONDS)
        node = self._make_node("node-boundary", boundary_hb, status="ONLINE")
        await fake_store.create_node(node)

        service = SyncthingService(Path("/tmp/fake"))
        await service._heartbeat_monitor_iteration(now=now)

        assert fake_store.nodes["node-boundary"].status == "ONLINE"
        assert fake_store.update_calls == [], "No update at boundary"

    @pytest.mark.asyncio
    async def test_no_redundant_write_when_already_online(self, fake_store):
        """Already ONLINE with fresh heartbeat → no update_node call."""
        now = datetime.now(timezone.utc)
        fresh_hb = now - timedelta(seconds=10)
        node = self._make_node("node-online", fresh_hb, status="ONLINE")
        await fake_store.create_node(node)

        service = SyncthingService(Path("/tmp/fake"))
        await service._heartbeat_monitor_iteration(now=now)

        assert fake_store.nodes["node-online"].status == "ONLINE"
        assert fake_store.update_calls == [], "No update for already ONLINE"

    @pytest.mark.asyncio
    async def test_no_redundant_write_when_already_offline(self, fake_store):
        """Already OFFLINE with stale heartbeat → no update_node call."""
        now = datetime.now(timezone.utc)
        stale_hb = now - timedelta(seconds=120)
        node = self._make_node("node-offline", stale_hb, status="OFFLINE")
        await fake_store.create_node(node)

        service = SyncthingService(Path("/tmp/fake"))
        await service._heartbeat_monitor_iteration(now=now)

        assert fake_store.nodes["node-offline"].status == "OFFLINE"
        assert fake_store.update_calls == [], "No update for already OFFLINE"
