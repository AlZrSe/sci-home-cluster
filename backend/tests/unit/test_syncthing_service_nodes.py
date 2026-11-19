"""
Unit tests for Syncthing service node processing and periodic rescan (issue #178).
"""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from backend.services.syncthing_service import SyncthingService
from shared.schemas.node_spec import NodeSpec, GPUInfo
from backend.store import get_store


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
    """Create a SyncthingService instance with a temp directory."""
    service = SyncthingService(temp_syncthing_root)
    yield service
    if service._running:
        await service.stop()


@pytest.fixture(autouse=True)
async def reset_store_fixture(seeding_disabled):
    """Reset store before each test using the seeding_disabled fixture."""
    yield


class TestNodeFileProcessing:
    """Tests for _process_node_file method."""

    @pytest.mark.asyncio
    async def test_process_node_file_creates_new_node(
        self, syncthing_service, temp_syncthing_root
    ):
        """New YAML file -> node created in store."""
        await syncthing_service.start()

        node_file = temp_syncthing_root / "nodes" / "node-new.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-new",
            hostname="new.lan",
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

        await syncthing_service._process_node_file("node-new", str(node_file))

        store = get_store()
        node = await store.get_node("node-new")
        assert node is not None
        assert node.node_id == "node-new"
        assert node.hostname == "new.lan"

    @pytest.mark.asyncio
    async def test_process_node_file_updates_existing_node(
        self, syncthing_service, temp_syncthing_root
    ):
        """Modified YAML file -> node updated in store."""
        await syncthing_service.start()

        node_file = temp_syncthing_root / "nodes" / "node-update.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-update",
            hostname="update.lan",
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

        # First process - creates node
        await syncthing_service._process_node_file("node-update", str(node_file))

        store = get_store()
        node = await store.get_node("node-update")
        assert node is not None
        assert node.hostname == "update.lan"
        assert node.cpus == 16

        # Update the spec
        node_spec.hostname = "updated-hostname.lan"
        node_spec.cpus = 32
        node_spec.status = "OFFLINE"
        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Second process - updates node
        await syncthing_service._process_node_file("node-update", str(node_file))

        node = await store.get_node("node-update")
        assert node.hostname == "updated-hostname.lan"
        assert node.cpus == 32
        assert node.status == "OFFLINE"

    @pytest.mark.asyncio
    async def test_process_node_file_idempotent(
        self, syncthing_service, temp_syncthing_root
    ):
        """Processing same file twice -> same store state."""
        await syncthing_service.start()

        node_file = temp_syncthing_root / "nodes" / "node-idempotent.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-idempotent",
            hostname="idempotent.lan",
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

        # Process first time
        await syncthing_service._process_node_file("node-idempotent", str(node_file))

        store = get_store()
        node1 = await store.get_node("node-idempotent")
        assert node1 is not None

        # Process second time (same file content)
        await syncthing_service._process_node_file("node-idempotent", str(node_file))

        node2 = await store.get_node("node-idempotent")
        assert node2 is not None
        assert node1.node_id == node2.node_id
        assert node1.hostname == node2.hostname
        assert node1.cpus == node2.cpus
        assert node1.status == node2.status

    @pytest.mark.asyncio
    async def test_process_node_file_malformed_yaml(
        self, syncthing_service, temp_syncthing_root, caplog
    ):
        """Invalid YAML -> warning logged, not added to _processed_files."""
        await syncthing_service.start()

        node_file = temp_syncthing_root / "nodes" / "node-bad.yaml"
        with open(node_file, "w") as f:
            f.write("invalid: yaml: content: [unclosed")

        await syncthing_service._process_node_file("node-bad", str(node_file))

        # Should log error
        error_logged = any(
            "Error processing node file" in record.message for record in caplog.records
        )
        assert error_logged
        # File should not be in processed_files (will be retried on rescan)
        assert str(node_file) not in syncthing_service._processed_files

        # Node should not be created
        store = get_store()
        node = await store.get_node("node-bad")
        assert node is None

    @pytest.mark.asyncio
    async def test_process_node_file_validation_error(
        self, syncthing_service, temp_syncthing_root, caplog
    ):
        """Missing required field -> warning logged, retried on rescan."""
        await syncthing_service.start()

        node_file = temp_syncthing_root / "nodes" / "node-invalid.yaml"
        # Missing required fields like node_id, hostname, etc.
        import yaml

        with open(node_file, "w") as f:
            yaml.dump({"node_id": "node-invalid"}, f)  # Incomplete spec

        await syncthing_service._process_node_file("node-invalid", str(node_file))

        # Should log error
        error_logged = any(
            "Error processing node file" in record.message for record in caplog.records
        )
        assert error_logged
        # File should not be in processed_files (will be retried on rescan)
        assert str(node_file) not in syncthing_service._processed_files

        # Node should not be created
        store = get_store()
        node = await store.get_node("node-invalid")
        assert node is None


class TestNodeDeletion:
    """Tests for _handle_node_deleted method."""

    @pytest.mark.asyncio
    async def test_handle_node_deleted_removes_from_store(
        self, syncthing_service, temp_syncthing_root
    ):
        """Deletion event -> node removed from store."""
        await syncthing_service.start()

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

        import yaml

        with open(node_file, "w") as f:
            yaml.dump(node_spec.model_dump(mode="json"), f)

        # Create node
        await syncthing_service._process_node_file("node-delete", str(node_file))

        store = get_store()
        node = await store.get_node("node-delete")
        assert node is not None

        # Delete node
        await syncthing_service._handle_node_deleted("node-delete")

        node = await store.get_node("node-delete")
        assert node is None


class TestInitialScan:
    """Tests for initial scan of nodes directory."""

    @pytest.mark.asyncio
    async def test_initial_scan_nodes_directory(
        self, syncthing_service, temp_syncthing_root
    ):
        """Startup scan processes all nodes/*.yaml files."""
        # Create multiple node files before starting
        for i in range(3):
            node_file = temp_syncthing_root / "nodes" / f"node-scan-{i}.yaml"
            gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
            node_spec = NodeSpec(
                node_id=f"node-scan-{i}",
                hostname=f"scan-{i}.lan",
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

        await syncthing_service.start()

        store = get_store()
        for i in range(3):
            node = await store.get_node(f"node-scan-{i}")
            assert node is not None
            assert node.node_id == f"node-scan-{i}"


class TestPeriodicRescan:
    """Tests for periodic rescan functionality (issue #178)."""

    @pytest.mark.asyncio
    async def test_periodic_rescan_detects_new_files(
        self, syncthing_service, temp_syncthing_root
    ):
        """Periodic scan finds files added while watcher was down."""
        await syncthing_service.start()

        store = get_store()

        # Verify our test node doesn't exist yet
        node = await store.get_node("node-rescan-new")
        assert node is None

        # Simulate file being added while watcher was down
        node_file = temp_syncthing_root / "nodes" / "node-rescan-new.yaml"
        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-rescan-new",
            hostname="rescan-new.lan",
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

        # Trigger manual scan (simulates periodic rescan)
        result = await syncthing_service.manual_scan()

        assert result["scanned"] >= 1

        node = await store.get_node("node-rescan-new")
        assert node is not None
        assert node.hostname == "rescan-new.lan"

    @pytest.mark.asyncio
    async def test_periodic_rescan_detects_deleted_files(
        self, syncthing_service, temp_syncthing_root
    ):
        """Periodic scan finds files deleted while watcher was down."""
        await syncthing_service.start()

        # Create a node file
        node_file = temp_syncthing_root / "nodes" / "node-rescan-del.yaml"
        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-rescan-del",
            hostname="rescan-del.lan",
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

        # Initial scan picks it up
        await syncthing_service.manual_scan()

        store = get_store()
        node = await store.get_node("node-rescan-del")
        assert node is not None

        # Simulate file being deleted while watcher was down
        node_file.unlink()

        # Trigger manual scan (simulates periodic rescan)
        await syncthing_service.manual_scan()

        node = await store.get_node("node-rescan-del")
        assert node is None


class TestManualScan:
    """Tests for manual_scan method."""

    @pytest.mark.asyncio
    async def test_manual_scan_nodes(self, syncthing_service, temp_syncthing_root):
        """manual_scan() processes nodes directory correctly."""
        await syncthing_service.start()

        # Create multiple node files
        for i in range(2):
            node_file = temp_syncthing_root / "nodes" / f"node-manual-{i}.yaml"
            gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
            node_spec = NodeSpec(
                node_id=f"node-manual-{i}",
                hostname=f"manual-{i}.lan",
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

        result = await syncthing_service.manual_scan()

        assert result["scanned"] >= 2
        assert result["total_processed"] >= 2

        store = get_store()
        for i in range(2):
            node = await store.get_node(f"node-manual-{i}")
            assert node is not None


class TestRescanTaskLifecycle:
    """Tests for periodic rescan task start/stop."""

    @pytest.mark.asyncio
    async def test_rescan_task_started_on_service_start(self, temp_syncthing_root):
        """Periodic rescan task is started when service starts."""
        service = SyncthingService(temp_syncthing_root)
        await service.start()

        assert service._rescan_task is not None
        assert not service._rescan_task.done()

        await service.stop()
        assert service._rescan_task.done()

    @pytest.mark.asyncio
    async def test_rescan_task_cancelled_on_service_stop(self, temp_syncthing_root):
        """Periodic rescan task is cancelled cleanly on service stop."""
        service = SyncthingService(temp_syncthing_root)
        await service.start()

        rescan_task = service._rescan_task
        assert rescan_task is not None

        await service.stop()

        assert rescan_task.done()
        assert rescan_task.cancelled()

    @pytest.mark.asyncio
    async def test_rescan_interval_from_settings(self, temp_syncthing_root):
        """Rescan interval is read from settings."""
        with patch("backend.services.syncthing_service.settings") as mock_settings:
            mock_settings.SYNCTHING_RESCAN_INTERVAL_SECONDS = 120
            mock_settings.HEARTBEAT_CHECK_INTERVAL_SECONDS = 30
            mock_settings.HEARTBEAT_TIMEOUT_SECONDS = 300

            service = SyncthingService(temp_syncthing_root)
            await service.start()

            assert service._rescan_interval_seconds == 120

            await service.stop()


class TestProcessNodeFileRaceCondition:
    """Tests for race condition handling in _process_node_file (issue #178)."""

    @pytest.mark.asyncio
    async def test_create_node_race_condition_handled(
        self, syncthing_service, temp_syncthing_root
    ):
        """Race between get_node and create_node is handled gracefully."""
        await syncthing_service.start()

        node_file = temp_syncthing_root / "nodes" / "node-race.yaml"

        gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
        node_spec = NodeSpec(
            node_id="node-race",
            hostname="race.lan",
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

        # Mock store to simulate race condition
        store = get_store()
        original_create = store.create_node
        call_count = 0

        async def mock_create_node(node_spec):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: simulate race by creating node externally
                await original_create(node_spec)
                # Then raise "already exists" as if another process created it
                raise ValueError(f"Node with ID {node_spec.node_id} already exists")
            return await original_create(node_spec)

        with patch.object(store, "create_node", side_effect=mock_create_node):
            await syncthing_service._process_node_file("node-race", str(node_file))

        # Should not raise, should handle race and update instead
        node = await store.get_node("node-race")
        assert node is not None
        assert node.hostname == "race.lan"
