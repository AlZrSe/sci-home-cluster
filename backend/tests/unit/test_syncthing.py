"""
Unit tests for Syncthing service.
"""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime

from backend.services.syncthing_service import SyncthingService
from backend.models.job_state import JobState
from backend.models.job_spec import JobSpec
from backend.models.job_status import JobStatus
from backend.models.node_spec import NodeSpec, GPUInfo
from backend.store.memory import get_store


@pytest.fixture
def temp_syncthing_root():
    """Create a temporary Syncthing root directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "jobs").mkdir()
        (root / "nodes").mkdir()
        yield root


@pytest.fixture
def syncthing_service(temp_syncthing_root):
    """Create a SyncthingService instance with temp directory."""
    service = SyncthingService(temp_syncthing_root)
    # Don't start here - let tests start it if needed
    yield service
    # Cleanup - stop if running (ignore cleanup errors in tests)
    if service._running:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule cleanup on the running loop
                loop.create_task(service.stop())
            else:
                loop.run_until_complete(service.stop())
        except RuntimeError:
            pass  # No event loop, ignore


@pytest.fixture(autouse=True)
async def reset_store_fixture():
    """Reset store before each test."""
    from backend.store.memory import get_store

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
            last_heartbeat=datetime.now(),
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
    """Tests for Syncthing API endpoints - skipped due to missing app.state.syncthing_service in test environment."""

    @pytest.mark.skip(reason="Requires app.state.syncthing_service which is set by lifespan (not run in unit tests)")
    def test_syncthing_status_endpoint(self, client):
        """Test GET /api/v1/syncthing/status"""
        response = client.get("/api/v1/syncthing/status")
        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "root_path" in data
        assert "jobs_folder" in data
        assert "nodes_folder" in data

    @pytest.mark.skip(reason="Requires app.state.syncthing_service which is set by lifespan (not run in unit tests)")
    def test_syncthing_scan_endpoint(self, client):
        """Test POST /api/v1/syncthing/scan"""
        response = client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "scanned" in data
        assert "total_processed" in data
