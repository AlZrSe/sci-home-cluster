"""
Integration tests for Syncthing: scan, status, YAML sync.
"""

import pytest
import pytest_asyncio
import tempfile
import yaml
from pathlib import Path
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.services.syncthing_service import SyncthingService
from shared.schemas.job_state import JobState
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec, GPUInfo
from backend.store import get_store


@pytest.mark.integration
class TestSyncthingFlow:
    """Integration tests for Syncthing service and API."""

    @pytest.fixture
    def temp_syncthing_root(self):
        """Create a temporary Syncthing root directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "jobs").mkdir()
            (root / "nodes").mkdir()
            yield root

    @pytest_asyncio.fixture
    async def syncthing_service(self, temp_syncthing_root):
        """Create and start a SyncthingService instance."""
        service = SyncthingService(temp_syncthing_root)
        await service.start()
        yield service
        await service.stop()

    @pytest_asyncio.fixture
    async def async_client(self) -> AsyncClient:
        """Create an async client for API testing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest_asyncio.fixture(autouse=True)
    async def reset_store(self):
        """Reset store before each test."""
        store = get_store()
        await store.reset()
        yield
        await store.reset()

    # ============================================================================
    # Syncthing Service Tests
    # ============================================================================

    @pytest.mark.asyncio
    async def test_service_start_stop(self, temp_syncthing_root):
        """Test service can start and stop properly."""
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
        # Create a job state file
        job_dir = temp_syncthing_root / "jobs" / "job-1001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="syncthing-test-job",
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
        assert job.spec.name == "syncthing-test-job"

    @pytest.mark.asyncio
    async def test_initial_scan_nodes(self, syncthing_service, temp_syncthing_root):
        """Test initial scan picks up node state files."""
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
    async def test_manual_scan_empty(self, syncthing_service):
        """Test manual scan on empty directories returns zero scanned."""
        result = await syncthing_service.manual_scan()
        assert result["scanned"] == 0
        assert "total_processed" in result

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
    async def test_invalid_job_id_ignored(self, syncthing_service, temp_syncthing_root):
        """Test that invalid job IDs are ignored during scan."""
        # Create a job with invalid ID
        job_dir = temp_syncthing_root / "jobs" / "invalid-job"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

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

    @pytest.mark.asyncio
    async def test_job_file_deletion(self, syncthing_service, temp_syncthing_root):
        """Test that deleting a job file removes it from store."""
        # First create and scan a job
        job_dir = temp_syncthing_root / "jobs" / "job-2001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="delete-test-job",
            command="python train.py",
            working_dir="/workspace",
            env={},
            resources={"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
            paths={"input": "/data/in", "output": "/data/out"},
            retry={"max_retries": 3, "retry_delay_seconds": 60},
        )

        job_state = JobState(
            job_id="job-2001",
            spec=job_spec,
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            retry_count=0,
        )

        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        # Verify job exists
        store = get_store()
        job = await store.get_job("job-2001")
        assert job is not None

        # Delete the file and scan again
        state_file.unlink()
        await syncthing_service.manual_scan()

        # Job should be removed from store
        job = await store.get_job("job-2001")
        assert job is None

    @pytest.mark.asyncio
    async def test_node_file_deletion(self, syncthing_service, temp_syncthing_root):
        """Test that deleting a node file removes it from store."""
        # First create and scan a node
        node_file = temp_syncthing_root / "nodes" / "node-delete-test.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-delete-test",
            hostname="delete-test.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        # Verify node exists
        store = get_store()
        node = await store.get_node("node-delete-test")
        assert node is not None

        # Delete the file and scan again
        node_file.unlink()
        await syncthing_service.manual_scan()

        # Node should be removed from store
        node = await store.get_node("node-delete-test")
        assert node is None

    @pytest.mark.asyncio
    async def test_job_update_from_yaml(self, syncthing_service, temp_syncthing_root):
        """Test that updating a job YAML file updates the store."""
        # Create initial job
        job_dir = temp_syncthing_root / "jobs" / "job-3001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="update-test-job",
            command="python train.py",
            working_dir="/workspace",
            env={},
            resources={"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
            paths={"input": "/data/in", "output": "/data/out"},
            retry={"max_retries": 3, "retry_delay_seconds": 60},
        )

        job_state = JobState(
            job_id="job-3001",
            spec=job_spec,
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            retry_count=0,
        )

        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        # Verify initial status
        store = get_store()
        job = await store.get_job("job-3001")
        assert job.status == JobStatus.PENDING

        # Update the YAML file with new status
        job_state.status = JobStatus.RUNNING
        job_state.node_id = "node-alpha"
        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        # Verify status updated
        job = await store.get_job("job-3001")
        assert job.status == JobStatus.RUNNING
        assert job.node_id == "node-alpha"

    # ============================================================================
    # Syncthing API Tests - SKIPPED due to missing app.state.syncthing_service in test environment
    # ============================================================================
    # These tests require the FastAPI lifespan to have run, which doesn't happen in test environment

    @pytest.mark.skip(
        reason="Requires app.state.syncthing_service which is set by lifespan (not run in integration tests)"
    )
    @pytest.mark.asyncio
    async def test_syncthing_status_endpoint(self, async_client):
        """Test GET /api/v1/syncthing/status returns correct structure."""
        response = await async_client.get("/api/v1/syncthing/status")
        assert response.status_code == 200

        data = response.json()
        assert "running" in data
        assert "root_path" in data
        assert "jobs_folder" in data
        assert "nodes_folder" in data

        # Check folder structure
        assert "exists" in data["jobs_folder"]
        assert "path" in data["jobs_folder"]
        assert "state_files" in data["jobs_folder"]
        assert "exists" in data["nodes_folder"]
        assert "path" in data["nodes_folder"]
        assert "state_files" in data["nodes_folder"]

    @pytest.mark.skip(
        reason="Requires app.state.syncthing_service which is set by lifespan (not run in integration tests)"
    )
    @pytest.mark.asyncio
    async def test_syncthing_scan_endpoint(self, async_client):
        """Test POST /api/v1/syncthing/scan triggers scan."""
        response = await async_client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200

        data = response.json()
        assert "message" in data
        assert "scanned" in data
        assert "total_processed" in data
        assert data["message"] == "Scan completed"

    @pytest.mark.skip(
        reason="Requires app.state.syncthing_service which is set by lifespan (not run in integration tests)"
    )
    @pytest.mark.asyncio
    async def test_syncthing_scan_picks_up_new_files(
        self, async_client, temp_syncthing_root
    ):
        """Test that scan endpoint picks up newly created files."""
        # Create a job file
        job_dir = temp_syncthing_root / "jobs" / "job-4001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="api-scan-test-job",
            command="python train.py",
            working_dir="/workspace",
            env={},
            resources={"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
            paths={"input": "/data/in", "output": "/data/out"},
            retry={"max_retries": 3, "retry_delay_seconds": 60},
        )

        job_state = JobState(
            job_id="job-4001",
            spec=job_spec,
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            retry_count=0,
        )

        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        # Trigger scan via API
        response = await async_client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200
        data = response.json()
        assert data["scanned"] >= 1

        # Verify job was loaded
        store = get_store()
        job = await store.get_job("job-4001")
        assert job is not None
        assert job.spec.name == "api-scan-test-job"

    @pytest.mark.skip(
        reason="Requires app.state.syncthing_service which is set by lifespan (not run in integration tests)"
    )
    @pytest.mark.asyncio
    async def test_syncthing_status_shows_file_counts(
        self, async_client, temp_syncthing_root
    ):
        """Test that status endpoint shows correct file counts."""
        # Create some files
        job_dir = temp_syncthing_root / "jobs" / "job-5001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="count-test-job",
            command="python train.py",
            working_dir="/workspace",
            env={},
            resources={"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
            paths={"input": "/data/in", "output": "/data/out"},
            retry={"max_retries": 3, "retry_delay_seconds": 60},
        )

        job_state = JobState(
            job_id="job-5001",
            spec=job_spec,
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            retry_count=0,
        )

        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        # Create a node file
        node_file = temp_syncthing_root / "nodes" / "node-count-test.yaml"
        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-count-test",
            hostname="count-test.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Get status
        response = await async_client.get("/api/v1/syncthing/status")
        assert response.status_code == 200
        data = response.json()

        # Should show at least 1 job state file and 1 node state file
        assert data["jobs_folder"]["state_files"] >= 1
        assert data["nodes_folder"]["state_files"] >= 1

    # ============================================================================
    # YAML Sync Integration Tests
    # ============================================================================

    @pytest.mark.asyncio
    async def test_full_yaml_sync_flow(self, syncthing_service, temp_syncthing_root):
        """Test complete YAML sync: create -> scan -> update -> scan -> delete -> scan."""
        store = get_store()

        # 1. Create job YAML and scan
        job_dir = temp_syncthing_root / "jobs" / "job-1001"
        job_dir.mkdir()
        state_file = job_dir / "state.yaml"

        job_spec = JobSpec(
            name="sync-flow-job",
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

        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        # Verify created
        job = await store.get_job("job-1001")
        assert job is not None
        assert job.status == JobStatus.PENDING

        # 2. Update job YAML to RUNNING and scan
        job_state.status = JobStatus.RUNNING
        job_state.node_id = "node-alpha"
        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        job = await store.get_job("job-1001")
        assert job.status == JobStatus.RUNNING
        assert job.node_id == "node-alpha"

        # 3. Update job YAML to COMPLETED and scan
        job_state.status = JobStatus.COMPLETED
        job_state.completed_at = datetime.now()
        job_state.exit_code = 0
        with open(state_file, "w") as f:
            yaml.dump(job_state.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        job = await store.get_job("job-1001")
        assert job.status == JobStatus.COMPLETED
        assert job.exit_code == 0

        # 4. Delete job YAML and scan
        state_file.unlink()
        job_dir.rmdir()

        await syncthing_service.manual_scan()

        # Verify deleted
        job = await store.get_job("job-1001")
        assert job is None

    @pytest.mark.asyncio
    async def test_node_yaml_sync_flow(self, syncthing_service, temp_syncthing_root):
        """Test node YAML sync: create -> scan -> update -> scan -> delete -> scan."""
        store = get_store()

        # 1. Create node YAML and scan
        node_file = temp_syncthing_root / "nodes" / "node-sync-001.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-sync-001",
            hostname="sync-001.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        # Verify created
        node = await store.get_node("node-sync-001")
        assert node is not None
        assert node.status == "ONLINE"

        # 2. Update node YAML to OFFLINE and scan
        node_spec.status = "OFFLINE"
        node_spec.current_job_id = "job-1234"
        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        await syncthing_service.manual_scan()

        node = await store.get_node("node-sync-001")
        assert node.status == "OFFLINE"
        assert node.current_job_id == "job-1234"

        # 3. Delete node YAML and scan
        node_file.unlink()

        await syncthing_service.manual_scan()

        # Verify deleted
        node = await store.get_node("node-sync-001")
        assert node is None


@pytest.mark.integration
class TestSyncthingAuthentication:
    """Tests for authentication on Syncthing endpoints - SKIPPED due to missing app.state.syncthing_service in test environment."""

    @pytest_asyncio.fixture
    async def unauthenticated_client(self) -> AsyncClient:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client

    @pytest.mark.skip(
        reason="Requires app.state.syncthing_service which is set by lifespan (not run in integration tests)"
    )
    @pytest.mark.asyncio
    async def test_syncthing_status_localhost_bypass(self, unauthenticated_client):
        """Test localhost bypass works for Syncthing status."""
        response = await unauthenticated_client.get("/api/v1/syncthing/status")
        assert response.status_code == 200

    @pytest.mark.skip(
        reason="Requires app.state.syncthing_service which is set by lifespan (not run in integration tests)"
    )
    @pytest.mark.asyncio
    async def test_syncthing_scan_localhost_bypass(self, unauthenticated_client):
        """Test localhost bypass works for Syncthing scan."""
        response = await unauthenticated_client.post("/api/v1/syncthing/scan")
        assert response.status_code == 200
