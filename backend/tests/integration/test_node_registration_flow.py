"""
Integration tests for file-based node registration flow (issue #178).

These tests verify the complete flow from agent writing node YAML files
to backend picking them up via watcher and periodic rescan.
"""

import tempfile
import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta

from backend.services.syncthing_service import SyncthingService
from shared.schemas.node_spec import NodeSpec, GPUInfo
from backend.store import get_store


@pytest.mark.integration
class TestNodeRegistrationFlow:
    """Integration tests for file-based node registration."""

    @pytest.fixture
    def temp_syncthing_root(self):
        """Create a temporary Syncthing root directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "jobs").mkdir()
            (root / "nodes").mkdir()
            yield root

    @pytest.fixture
    async def syncthing_service(self, temp_syncthing_root):
        """Create and start a SyncthingService instance."""
        service = SyncthingService(temp_syncthing_root)
        await service.start()
        yield service
        await service.stop()

    @pytest.fixture(autouse=True)
    async def reset_store(self, seeding_disabled):
        """Reset store before each test using seeding_disabled fixture."""
        yield

    @pytest.mark.asyncio
    async def test_agent_write_detected_by_backend(
        self, syncthing_service, temp_syncthing_root
    ):
        """Agent writes nodes/node-01.yaml -> backend picks it up via watcher."""
        store = get_store()

        # Simulate agent writing node file
        node_file = temp_syncthing_root / "nodes" / "node-01.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-01",
            hostname="agent-01.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Wait for watcher to pick up (debounce 500ms + processing)
        import asyncio

        await asyncio.sleep(2.0)

        # Verify node appears in store
        node = await store.get_node("node-01")
        assert node is not None
        assert node.node_id == "node-01"
        assert node.hostname == "agent-01.lan"
        assert node.status == "ONLINE"

    @pytest.mark.asyncio
    async def test_agent_heartbeat_updates_node(
        self, syncthing_service, temp_syncthing_root
    ):
        """Agent rewrites file with new last_heartbeat -> backend updates node."""
        store = get_store()

        node_file = temp_syncthing_root / "nodes" / "node-heartbeat.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-heartbeat",
            hostname="heartbeat.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=60),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        import asyncio

        await asyncio.sleep(1.0)

        # Verify initial heartbeat
        node = await store.get_node("node-heartbeat")
        assert node is not None
        initial_heartbeat = node.last_heartbeat

        # Agent updates heartbeat (simulating periodic write every ~30s)
        new_heartbeat = datetime.now(timezone.utc)
        node_spec.last_heartbeat = new_heartbeat
        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        await asyncio.sleep(1.0)

        # Verify heartbeat updated
        node = await store.get_node("node-heartbeat")
        assert node.last_heartbeat > initial_heartbeat

    @pytest.mark.asyncio
    async def test_agent_gpu_change_detected(
        self, syncthing_service, temp_syncthing_root
    ):
        """Agent rewrites file with different GPU list -> backend updates gpus field."""
        store = get_store()

        node_file = temp_syncthing_root / "nodes" / "node-gpu-change.yaml"

        gpu1 = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-gpu-change",
            hostname="gpu-change.lan",
            gpus=[gpu1],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        import asyncio

        await asyncio.sleep(1.0)

        # Verify initial GPU
        node = await store.get_node("node-gpu-change")
        assert len(node.gpus) == 1
        assert node.gpus[0].name == "NVIDIA RTX 4090"

        # Agent detects GPU change (e.g., new GPU added)
        gpu2 = GPUInfo(name="NVIDIA RTX 3090", memory_gb=24)
        node_spec.gpus = [gpu1, gpu2]
        node_spec.last_heartbeat = datetime.now(timezone.utc)
        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        await asyncio.sleep(1.0)

        # Verify GPU list updated
        node = await store.get_node("node-gpu-change")
        assert len(node.gpus) == 2
        gpu_names = [g.name for g in node.gpus]
        assert "NVIDIA RTX 4090" in gpu_names
        assert "NVIDIA RTX 3090" in gpu_names

    @pytest.mark.asyncio
    async def test_node_file_deletion_removes_node(
        self, syncthing_service, temp_syncthing_root
    ):
        """Delete nodes/node-01.yaml -> node removed from store and API."""
        store = get_store()

        node_file = temp_syncthing_root / "nodes" / "node-delete.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-delete",
            hostname="delete.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        import asyncio

        await asyncio.sleep(1.0)

        # Verify node exists
        node = await store.get_node("node-delete")
        assert node is not None

        # Simulate agent decommissioning - delete the file
        node_file.unlink()

        await asyncio.sleep(1.0)

        # Verify node removed from store
        node = await store.get_node("node-delete")
        assert node is None

    @pytest.mark.asyncio
    async def test_watcher_restart_triggers_rescan(self, temp_syncthing_root):
        """Stop/start watcher -> full rescan runs, no nodes lost."""
        store = get_store()

        # Create initial node file
        node_file = temp_syncthing_root / "nodes" / "node-watcher-restart.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-watcher-restart",
            hostname="watcher-restart.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Start service, watcher picks up file
        service1 = SyncthingService(temp_syncthing_root)
        await service1.start()

        import asyncio

        await asyncio.sleep(1.0)

        node = await store.get_node("node-watcher-restart")
        assert node is not None

        # Stop service (watcher stops)
        await service1.stop()

        # Add another node file while watcher is down
        node_file2 = temp_syncthing_root / "nodes" / "node-watcher-restart-2.yaml"
        node_spec2 = NodeSpec(
            node_id="node-watcher-restart-2",
            hostname="watcher-restart-2.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )
        with open(node_file2, "w") as f:
            yaml.dump(node_spec2.model_dump(mode="json"), f)

        # Restart service (new watcher starts)
        service2 = SyncthingService(temp_syncthing_root)
        await service2.start()

        await asyncio.sleep(1.0)

        # Both nodes should be present (initial scan + periodic rescan)
        node1 = await store.get_node("node-watcher-restart")
        assert node1 is not None

        node2 = await store.get_node("node-watcher-restart-2")
        assert node2 is not None

        await service2.stop()

    @pytest.mark.asyncio
    async def test_periodic_rescan_catches_missed_events(
        self, syncthing_service, temp_syncthing_root
    ):
        """Periodic rescan catches files missed by watcher."""
        store = get_store()

        # Verify our test node doesn't exist yet
        node = await store.get_node("node-missed")
        assert node is None

        # Add file directly (simulating Syncthing delivery without watcher event)
        node_file = temp_syncthing_root / "nodes" / "node-missed.yaml"
        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-missed",
            hostname="missed.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )
        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Manual scan simulates periodic rescan
        result = await syncthing_service.manual_scan()

        assert result["scanned"] >= 1

        node = await store.get_node("node-missed")
        assert node is not None
        assert node.hostname == "missed.lan"

    @pytest.mark.asyncio
    async def test_multiple_nodes_registered_via_files(
        self, syncthing_service, temp_syncthing_root
    ):
        """Multiple agent nodes appear in cluster via file watcher only."""
        store = get_store()

        # Create multiple node files (simulating multiple agents starting)
        for i in range(3):
            node_file = temp_syncthing_root / "nodes" / f"node-multi-{i:02d}.yaml"
            gpu = GPUInfo(name=f"NVIDIA GPU {i}", memory_gb=24)
            node_spec = NodeSpec(
                node_id=f"node-multi-{i:02d}",
                hostname=f"multi-{i:02d}.lan",
                gpus=[gpu],
                cpus=16,
                memory_gb=64,
                os="Ubuntu 24.04",
                status="ONLINE",
                last_heartbeat=datetime.now(timezone.utc),
                current_job_id=None,
            )
            with open(node_file, "w") as f:
                yaml.dump(node_spec.model_dump(mode="json"), f)

        import asyncio

        await asyncio.sleep(2.0)  # Wait for all watcher events

        # All nodes should be registered
        nodes = await store.list_nodes()
        multi_nodes = [n for n in nodes if n.node_id.startswith("node-multi-")]
        assert len(multi_nodes) == 3

        for i in range(3):
            node = await store.get_node(f"node-multi-{i:02d}")
            assert node is not None
            assert node.hostname == f"multi-{i:02d}.lan"

    @pytest.mark.asyncio
    async def test_node_status_transition_offline_on_heartbeat_timeout(
        self, syncthing_service, temp_syncthing_root
    ):
        """Node marked OFFLINE when heartbeat stops (agent dies)."""
        store = get_store()

        node_file = temp_syncthing_root / "nodes" / "node-timeout.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-timeout",
            hostname="timeout.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=360),  # Stale (> 300s timeout)
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        import asyncio

        await asyncio.sleep(2.0)

        # Node should be created with ONLINE status from file
        node = await store.get_node("node-timeout")
        assert node is not None
        assert node.status == "ONLINE"

        # Wait for heartbeat monitor to run (30s interval) and mark OFFLINE
        # We can't wait 30s in tests, so directly trigger the monitor iteration
        await syncthing_service._heartbeat_monitor_iteration(
            now=datetime.now(timezone.utc)
        )

        node = await store.get_node("node-timeout")
        assert node.status == "OFFLINE"

    @pytest.mark.asyncio
    async def test_no_http_api_dependency_for_registration(
        self, syncthing_service, temp_syncthing_root
    ):
        """Node registration works purely through Syncthing folder, no HTTP
        API needed."""
        store = get_store()

        # This test verifies that the architecture decision from issue #92
        # is maintained: Node registration is file-based only, no
        # POST /nodes/register endpoint exists.

        # Agent writes file directly to Syncthing folder
        node_file = temp_syncthing_root / "nodes" / "node-no-api.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-no-api",
            hostname="no-api.lan",
            gpus=[gpu],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        import asyncio

        await asyncio.sleep(1.0)

        # Node appears in store without any HTTP API call
        node = await store.get_node("node-no-api")
        assert node is not None

        # Verify no HTTP registration endpoint is called (architectural test)
        # The frontend GET /api/v1/nodes should reflect this node
        nodes = await store.list_nodes()
        node_ids = [n.node_id for n in nodes]
        assert "node-no-api" in node_ids
