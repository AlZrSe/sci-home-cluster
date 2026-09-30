"""
Unit tests for the database store.
Uses factory-boy for test data generation.
"""

import pytest
from backend.store import DatabaseStore
from shared.schemas.job_status import JobStatus
from backend.tests.factories import (
    JobSpecFactory,
    NodeSpecFactory,
    ResourcesFactory,
    create_job_spec,
    create_node_spec,
    create_gpu_info,
)


@pytest.fixture
async def store(clean_database):
    """
    A DatabaseStore with no data, seeded lazily on first use.

    Each test starts from empty tables, so these tests exercise the store
    the application actually runs rather than a parallel in-memory
    implementation.
    """
    return DatabaseStore()


@pytest.fixture
async def empty_store(clean_database):
    """A DatabaseStore that stays empty: the seeder is suppressed."""
    store = DatabaseStore()
    store._seeded = True
    return store


@pytest.fixture
def sample_node():
    """Create a sample node spec using factory."""
    return create_node_spec(node_id="test-node", gpus=1, cpus=4, memory_gb=16)


@pytest.fixture
def sample_job_spec():
    """Create a sample job spec using factory."""
    return create_job_spec(name="test-job", gpus=1, cpus=4, memory_gb=16)


class TestNodeCRUD:
    """Test node CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_node(self, store, sample_node):
        """Test creating a new node."""
        result = await store.create_node(sample_node)

        assert result.node_id == sample_node.node_id
        assert result.hostname == sample_node.hostname
        assert len(result.gpus) == len(sample_node.gpus)
        assert result.gpus[0].name == sample_node.gpus[0].name
        assert result.cpus == sample_node.cpus
        assert result.memory_gb == sample_node.memory_gb
        assert result.os == sample_node.os
        assert result.status == sample_node.status

        stored_node = await store.get_node(sample_node.node_id)
        assert stored_node is not None
        assert stored_node.node_id == sample_node.node_id

    @pytest.mark.asyncio
    async def test_create_node_duplicate(self, store, sample_node):
        """Test creating a node that already exists raises an error."""
        await store.create_node(sample_node)

        with pytest.raises(ValueError, match="already exists"):
            await store.create_node(sample_node)

    @pytest.mark.asyncio
    async def test_get_node(self, store, sample_node):
        """Test getting a node by ID."""
        await store.create_node(sample_node)

        result = await store.get_node(sample_node.node_id)

        assert result is not None
        assert result.node_id == sample_node.node_id
        assert result.hostname == sample_node.hostname

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, store):
        """Test getting a non-existent node returns None."""
        result = await store.get_node("non-existent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_node(self, store, sample_node):
        """Test updating a node's fields."""
        await store.create_node(sample_node)

        result = await store.update_node(
            sample_node.node_id, hostname="updated.lan", status="OFFLINE"
        )

        assert result is not None
        assert result.hostname == "updated.lan"
        assert result.status == "OFFLINE"
        assert result.node_id == sample_node.node_id
        assert result.cpus == sample_node.cpus

    @pytest.mark.asyncio
    async def test_update_node_partial(self, store, sample_node):
        """Test updating only some node fields."""
        await store.create_node(sample_node)

        result = await store.update_node(sample_node.node_id, status="OFFLINE")

        assert result is not None
        assert result.status == "OFFLINE"
        assert result.hostname == sample_node.hostname  # Unchanged
        assert result.cpus == sample_node.cpus

    @pytest.mark.asyncio
    async def test_update_node_not_found(self, store):
        """Test updating a non-existent node returns None."""
        result = await store.update_node("non-existent", hostname="test.lan")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_node(self, store, sample_node):
        """Test deleting a node."""
        await store.create_node(sample_node)

        result = await store.delete_node(sample_node.node_id)

        assert result is True
        stored_node = await store.get_node(sample_node.node_id)
        assert stored_node is None

    @pytest.mark.asyncio
    async def test_delete_node_not_found(self, store):
        """Test deleting a non-existent node returns False."""
        result = await store.delete_node("non-existent")
        assert result is False

    @pytest.mark.asyncio
    async def test_create_node_with_factory_variations(self, store):
        """Test creating nodes with various factory configurations."""
        # Node with multiple GPUs
        node1 = create_node_spec(node_id="multi-gpu", gpus=4, cpus=32, memory_gb=128)
        result1 = await store.create_node(node1)
        assert len(result1.gpus) == 4

        # Node with no GPUs (CPU only)
        node2 = create_node_spec(node_id="cpu-only", gpus=0, cpus=16, memory_gb=64)
        result2 = await store.create_node(node2)
        assert result2.gpus == []

        # Node with custom GPU
        custom_gpu = create_gpu_info(name="Custom GPU", memory_gb=48)
        node3 = NodeSpecFactory(node_id="custom-gpu", gpus=[custom_gpu])
        result3 = await store.create_node(node3)
        assert result3.gpus[0].name == "Custom GPU"
        assert result3.gpus[0].memory_gb == 48

    @pytest.mark.asyncio
    async def test_list_nodes_empty_store(self, empty_store):
        """Test listing nodes on a fresh empty store."""
        nodes = await empty_store.list_nodes()
        assert nodes == []


class TestJobCRUD:
    """Test job CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_job(self, store, sample_job_spec):
        """Test creating a new job."""
        result = await store.create_job(sample_job_spec)

        assert result.job_id is not None
        assert result.job_id.startswith("job-")
        assert result.spec.name == sample_job_spec.name
        assert result.spec.command == sample_job_spec.command
        assert result.status == JobStatus.PENDING
        assert result.retry_count == 0
        assert result.created_at is not None

        stored_job = await store.get_job(result.job_id)
        assert stored_job is not None
        assert stored_job.job_id == result.job_id

    @pytest.mark.asyncio
    async def test_create_job_with_factory(self, store):
        """Test creating a job using JobSpecFactory directly."""
        job_spec = JobSpecFactory(
            name="factory-job",
            resources=ResourcesFactory(gpus=2, cpus=8, memory_gb=32),
        )
        result = await store.create_job(job_spec)

        assert result.spec.name == "factory-job"
        assert result.spec.resources.gpus == 2
        assert result.spec.resources.cpus == 8
        assert result.spec.resources.memory_gb == 32

    @pytest.mark.asyncio
    async def test_get_job(self, store, sample_job_spec):
        """Test getting a job by ID."""
        created_job = await store.create_job(sample_job_spec)

        result = await store.get_job(created_job.job_id)

        assert result is not None
        assert result.job_id == created_job.job_id
        assert result.spec.name == sample_job_spec.name

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, store):
        """Test getting a non-existent job returns None."""
        result = await store.get_job("non-existent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_jobs(self, store, sample_job_spec):
        """Test listing jobs with filtering."""
        job1 = await store.create_job(sample_job_spec)
        job2_spec = sample_job_spec.model_copy(update={"name": "test-job-2"})
        job2 = await store.create_job(job2_spec)

        result, total = await store.list_jobs()

        assert total >= 12  # 10 seed jobs + 2 test jobs
        assert len(result) >= 2
        job_ids = {job.job_id for job in result}
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids

    @pytest.mark.asyncio
    async def test_list_jobs_with_limit_offset(self, store, sample_job_spec):
        """Test listing jobs with pagination."""
        # Create multiple jobs
        for i in range(5):
            spec = sample_job_spec.model_copy(update={"name": f"test-job-{i}"})
            await store.create_job(spec)

        # Test limit
        result, total = await store.list_jobs(limit=3, offset=0)
        assert len(result) == 3

        # Test offset
        result2, _ = await store.list_jobs(limit=3, offset=3)
        assert len(result2) <= 3

        # Verify no overlap
        ids1 = {j.job_id for j in result}
        ids2 = {j.job_id for j in result2}
        assert ids1.isdisjoint(ids2)

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(self, store, sample_job_spec):
        """Test listing jobs filtered by status."""
        job1 = await store.create_job(sample_job_spec)
        job2 = await store.create_job(sample_job_spec)

        await store.update_job(job1.job_id, status=JobStatus.RUNNING)

        running_jobs, running_total = await store.list_jobs(status="RUNNING")
        pending_jobs, pending_total = await store.list_jobs(status="PENDING")

        running_job_ids = {job.job_id for job in running_jobs}
        pending_job_ids = {job.job_id for job in pending_jobs}

        assert job1.job_id in running_job_ids
        assert job2.job_id in pending_job_ids
        assert job1.job_id not in pending_job_ids

    @pytest.mark.asyncio
    async def test_list_jobs_by_node(self, store, sample_job_spec, sample_node):
        """Test listing jobs filtered by node."""
        await store.create_node(sample_node)

        job1 = await store.create_job(sample_job_spec)
        job2 = await store.create_job(sample_job_spec)

        await store.update_job(
            job1.job_id, status=JobStatus.RUNNING, node_id=sample_node.node_id
        )

        node_jobs, node_total = await store.list_jobs(node_id=sample_node.node_id)
        other_jobs, other_total = await store.list_jobs(node_id="other-node")

        node_job_ids = {job.job_id for job in node_jobs}
        assert job1.job_id in node_job_ids
        assert job2.job_id not in node_job_ids

    @pytest.mark.asyncio
    async def test_list_jobs_by_search(self, store):
        """Test listing jobs filtered by search term."""
        job_spec1 = create_job_spec(name="training-run-1")
        job_spec2 = create_job_spec(name="inference-batch-2")
        job_spec3 = create_job_spec(name="training-run-3")

        job1 = await store.create_job(job_spec1)
        job2 = await store.create_job(job_spec2)
        job3 = await store.create_job(job_spec3)

        training_jobs, _ = await store.list_jobs(search="training")
        training_ids = {j.job_id for j in training_jobs}
        assert job1.job_id in training_ids
        assert job3.job_id in training_ids
        assert job2.job_id not in training_ids

    @pytest.mark.asyncio
    async def test_list_jobs_invalid_status(self, store, sample_job_spec):
        """Test listing jobs with invalid status returns empty."""
        await store.create_job(sample_job_spec)

        result, total = await store.list_jobs(status="INVALID_STATUS")
        assert total == 0
        assert result == []

    @pytest.mark.asyncio
    async def test_update_job(self, store, sample_job_spec):
        """Test updating a job's fields."""
        created_job = await store.create_job(sample_job_spec)

        result = await store.update_job(
            created_job.job_id, status=JobStatus.RUNNING, node_id="test-node"
        )

        assert result is not None
        assert result.status == JobStatus.RUNNING
        assert result.node_id == "test-node"
        assert result.job_id == created_job.job_id
        assert result.spec.name == sample_job_spec.name

    @pytest.mark.asyncio
    async def test_update_job_partial(self, store, sample_job_spec):
        """Test updating only some job fields."""
        created_job = await store.create_job(sample_job_spec)
        original_name = created_job.spec.name

        result = await store.update_job(created_job.job_id, status=JobStatus.RUNNING)

        assert result.status == JobStatus.RUNNING
        assert result.spec.name == original_name  # Unchanged

    @pytest.mark.asyncio
    async def test_update_job_not_found(self, store):
        """Test updating a non-existent job returns None."""
        result = await store.update_job("non-existent", status=JobStatus.RUNNING)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_job(self, store, sample_job_spec):
        """Test deleting a job."""
        created_job = await store.create_job(sample_job_spec)

        result = await store.delete_job(created_job.job_id)

        assert result is True
        stored_job = await store.get_job(created_job.job_id)
        assert stored_job is None

    @pytest.mark.asyncio
    async def test_delete_job_cleans_up_associated_data(self, store, sample_job_spec):
        """Test that deleting a job also cleans up metrics and logs."""
        from sqlalchemy import func, select

        from backend.core.database import get_session
        from backend.store.database import (
            CPUMetricModel,
            GPUMetricModel,
            LogEntryModel,
        )

        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        # Generate some metrics and logs
        await store.get_job_metrics(job_id)
        await store.get_job_logs(job_id)

        async def counts() -> tuple:
            async with get_session() as session:
                gpu = await session.execute(
                    select(func.count())
                    .select_from(GPUMetricModel)
                    .where(GPUMetricModel.job_id == job_id)
                )
                cpu = await session.execute(
                    select(func.count())
                    .select_from(CPUMetricModel)
                    .where(CPUMetricModel.job_id == job_id)
                )
                logs = await session.execute(
                    select(func.count())
                    .select_from(LogEntryModel)
                    .where(LogEntryModel.job_id == job_id)
                )
                return gpu.scalar(), cpu.scalar(), logs.scalar()

        # Verify they exist
        gpu_before, cpu_before, logs_before = await counts()
        assert gpu_before > 0
        assert cpu_before > 0
        assert logs_before > 0

        # Delete job
        assert await store.delete_job(job_id) is True

        # Verify cleaned up
        assert await counts() == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_delete_job_not_found(self, store):
        """Test deleting a non-existent job returns False."""
        result = await store.delete_job("non-existent")
        assert result is False

    @pytest.mark.asyncio
    async def test_job_id_generation_sequential(self, store, sample_job_spec):
        """Test that job IDs are generated sequentially."""
        job1 = await store.create_job(sample_job_spec)
        job2 = await store.create_job(sample_job_spec)
        job3 = await store.create_job(sample_job_spec)

        num1 = int(job1.job_id.split("-")[1])
        num2 = int(job2.job_id.split("-")[1])
        num3 = int(job3.job_id.split("-")[1])

        assert num2 == num1 + 1
        assert num3 == num2 + 1


class TestMetrics:
    """Test metrics generation and caching."""

    @pytest.mark.asyncio
    async def test_get_job_metrics_creates_and_caches(self, store, sample_job_spec):
        """Test that metrics are generated and cached."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        # First call generates metrics
        metrics1 = await store.get_job_metrics(job_id)
        assert metrics1 is not None
        assert metrics1.job_id == job_id
        assert len(metrics1.gpu_metrics) > 0
        assert len(metrics1.cpu_metrics) > 0
        assert metrics1.summary.gpu_memory_avg_mb > 0

        # Second call must return the same data. The database store
        # rebuilds the model from stored rows rather than handing back a
        # cached instance, so compare values, not object identity.
        metrics2 = await store.get_job_metrics(job_id)
        assert metrics2 is not None
        assert metrics2 == metrics1

    @pytest.mark.asyncio
    async def test_get_job_metrics_not_found(self, store):
        """Test getting metrics for non-existent job returns None."""
        metrics = await store.get_job_metrics("job-999999")
        assert metrics is None

    @pytest.mark.asyncio
    async def test_metrics_deterministic_per_job(self, store):
        """Test that metrics are deterministic for the same job ID."""
        job_spec = create_job_spec(name="metrics-test")

        job1 = await store.create_job(job_spec)
        job2 = await store.create_job(job_spec)

        metrics1 = await store.get_job_metrics(job1.job_id)
        metrics2 = await store.get_job_metrics(job2.job_id)

        # Different jobs should have different metrics (different seeds)
        assert metrics1.job_id != metrics2.job_id
        # But same job should always return same metrics
        metrics1_again = await store.get_job_metrics(job1.job_id)
        assert (
            metrics1.gpu_metrics[0].memory_used_mb
            == metrics1_again.gpu_metrics[0].memory_used_mb
        )


class TestLogs:
    """Test log generation and history."""

    @pytest.mark.asyncio
    async def test_get_job_logs_creates_and_caches(self, store, sample_job_spec):
        """Test that logs are generated and cached."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        # First call generates logs
        logs1 = await store.get_job_logs(job_id)
        assert isinstance(logs1, list)
        assert len(logs1) > 0

        # Second call must return the same lines. The database store
        # re-reads them from storage rather than handing back a cached
        # list, so compare values, not object identity.
        logs2 = await store.get_job_logs(job_id)
        assert logs2 == logs1

    @pytest.mark.asyncio
    async def test_get_job_logs_not_found(self, store):
        """Test getting logs for non-existent job returns generated logs (current behavior)."""
        # The store generates logs on-the-fly for any job ID
        logs = await store.get_job_logs("job-999999")
        assert isinstance(logs, list)
        assert len(logs) > 0

    @pytest.mark.asyncio
    async def test_log_format(self, store, sample_job_spec):
        """Test log lines have expected format."""
        created_job = await store.create_job(sample_job_spec)
        logs = await store.get_job_logs(created_job.job_id)

        for log_line in logs:
            assert isinstance(log_line, str)
            assert len(log_line) > 0
            # Should start with timestamp
            parts = log_line.split(" ", 2)
            assert len(parts) >= 3

    @pytest.mark.asyncio
    async def test_logs_deterministic_per_job(self, store):
        """Test that logs are deterministic for the same job ID."""
        job_spec = create_job_spec(name="logs-test")

        job1 = await store.create_job(job_spec)
        job2 = await store.create_job(job_spec)

        logs1 = await store.get_job_logs(job1.job_id)
        logs2 = await store.get_job_logs(job2.job_id)

        # Different jobs should have different logs
        assert logs1 != logs2
        # But same job should always return same logs
        logs1_again = await store.get_job_logs(job1.job_id)
        assert logs1 == logs1_again


class TestLogStreaming:
    """Test WebSocket log streaming simulation."""

    @pytest.mark.asyncio
    async def test_start_log_stream_creates_task(self, store, sample_job_spec):
        """Test that start_log_stream creates a background task."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        # Set job to RUNNING to enable streaming
        await store.update_job(job_id, status=JobStatus.RUNNING, node_id="node-alpha")

        received_lines = []

        def on_line(line: str):
            received_lines.append(line)

        task = await store.start_log_stream(job_id, on_line)

        assert task is not None
        assert not task.done()

        # Wait for some logs to be generated. The stream worker ticks on an
        # interval and reads the job from the database first, so poll rather
        # than assuming a fixed delay is enough.
        for _ in range(50):
            if received_lines:
                break
            await asyncio.sleep(0.1)

        # Clean up
        await store._stop_log_stream(job_id)

        # Should have received some log lines
        assert len(received_lines) > 0

    @pytest.mark.asyncio
    async def test_stop_log_stream_cancels_task(self, store, sample_job_spec):
        """Test that _stop_log_stream cancels the streaming task."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        await store.update_job(job_id, status=JobStatus.RUNNING)

        def on_line(line: str):
            pass

        await store.start_log_stream(job_id, on_line)
        assert job_id in store._log_stream_tasks

        await store._stop_log_stream(job_id)

        assert job_id not in store._log_stream_tasks

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_log_stream(self, store, sample_job_spec):
        """Test subscribing and unsubscribing from log stream."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        await store.update_job(job_id, status=JobStatus.RUNNING)

        received = []

        def callback(line: str):
            received.append(line)

        store.subscribe_to_log_stream(job_id, callback)
        assert job_id in store._log_stream_subscribers
        assert callback in store._log_stream_subscribers[job_id]

        store.unsubscribe_from_log_stream(job_id, callback)
        assert job_id not in store._log_stream_subscribers

    @pytest.mark.asyncio
    async def test_log_stream_stops_when_job_not_running(self, store, sample_job_spec):
        """Test that log streaming stops when job is no longer RUNNING."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        await store.update_job(job_id, status=JobStatus.RUNNING)

        received = []

        def on_line(line: str):
            received.append(line)

        await store.start_log_stream(job_id, on_line)
        await asyncio.sleep(0.3)

        # Change job status to COMPLETED
        await store.update_job(job_id, status=JobStatus.COMPLETED, exit_code=0)

        # Give time for stream to stop
        await asyncio.sleep(2.0)

        # Task should be cleaned up
        assert job_id not in store._log_stream_tasks


class TestStoreReset:
    """Test store reset functionality."""

    @pytest.mark.asyncio
    async def test_reset_clears_all_data(self, store, sample_node, sample_job_spec):
        """Test resetting the store clears all data."""
        await store.create_node(sample_node)
        job = await store.create_job(sample_job_spec)

        assert await store.get_node("test-node") is not None
        assert await store.get_job(job.job_id) is not None

        await store.reset()

        assert await store.get_node("test-node") is None
        assert await store.get_job(job.job_id) is None

    @pytest.mark.asyncio
    async def test_reset_restores_seed_data(self, store):
        """Test that reset restores seed data."""
        # Add some data first
        node = create_node_spec(node_id="temp-node")
        await store.create_node(node)
        job_spec = create_job_spec(name="temp-job")
        await store.create_job(job_spec)

        await store.reset()

        # Seed data should be present
        nodes = await store.list_nodes()
        assert len(nodes) >= 4

        jobs, total = await store.list_jobs()
        assert total >= 10

    @pytest.mark.asyncio
    async def test_reset_job_counter(self, store, sample_job_spec):
        """Test that job counter is reset to seed value."""
        job = await store.create_job(sample_job_spec)
        counter_after_create = store._job_counter

        await store.reset()

        # Counter should be reset to seed value (1050)
        assert store._job_counter == 1050

    @pytest.mark.asyncio
    async def test_reset_clears_log_stream_tasks(self, store, sample_job_spec):
        """Test that reset cancels and clears log stream tasks."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        await store.update_job(job_id, status=JobStatus.RUNNING)

        def on_line(line: str):
            pass

        await store.start_log_stream(job_id, on_line)
        assert job_id in store._log_stream_tasks

        await store.reset()

        assert job_id not in store._log_stream_tasks
        assert job_id not in store._log_stream_subscribers


class TestSeedData:
    """Test that seed data matches expectations."""

    @pytest.mark.asyncio
    async def test_seed_nodes_exist(self, store):
        """Test that expected seed nodes are present."""
        nodes = await store.list_nodes()

        node_ids = {node.node_id for node in nodes}
        expected_nodes = {"node-alpha", "node-beta", "node-gamma", "node-delta"}
        assert expected_nodes.issubset(node_ids)

        alpha = await store.get_node("node-alpha")
        assert alpha is not None
        assert alpha.hostname == "alpha.lan"
        assert alpha.status == "ONLINE"
        assert len(alpha.gpus) == 1
        assert alpha.gpus[0].name == "NVIDIA RTX 4090"
        assert alpha.gpus[0].memory_gb == 24
        assert alpha.cpus == 16
        assert alpha.memory_gb == 64

    @pytest.mark.asyncio
    async def test_seed_jobs_exist(self, store):
        """Test that expected seed jobs are present."""
        jobs, total = await store.list_jobs()

        assert total >= 10

        expected_job_ids = {f"job-{1050 - i}" for i in range(10)}
        actual_job_ids = {job.job_id for job in jobs}
        assert expected_job_ids.issubset(actual_job_ids)

        job_1050 = await store.get_job("job-1050")
        assert job_1050 is not None
        assert job_1050.spec.name == "protein-fold-batch"
        assert job_1050.status == JobStatus.RUNNING
        assert job_1050.node_id is not None

    @pytest.mark.asyncio
    async def test_seed_data_deterministic(self, store):
        """
        Test that generated data is deterministic for the same input.

        The generators used to be seeded from builtin hash(), which is
        salted per process, so two store instances could disagree. They
        are now seeded from a stable CRC32.
        """
        metrics1 = await store._generate_job_metrics("job-1050")
        metrics2 = await store._generate_job_metrics("job-1050")
        assert metrics1 is not None and metrics2 is not None

        assert [m.memory_used_mb for m in metrics1.gpu_metrics] == [
            m.memory_used_mb for m in metrics2.gpu_metrics
        ]
        assert [m.utilization_percent for m in metrics1.gpu_metrics] == [
            m.utilization_percent for m in metrics2.gpu_metrics
        ]
        assert [m.temperature_c for m in metrics1.gpu_metrics] == [
            m.temperature_c for m in metrics2.gpu_metrics
        ]
        assert [m.cpu_percent for m in metrics1.cpu_metrics] == [
            m.cpu_percent for m in metrics2.cpu_metrics
        ]

        # Log lines are stamped with the current time, so compare the
        # generated message bodies rather than the raw lines.
        def bodies(lines: list) -> list:
            return [line[19:] for line in lines]

        logs1 = store._generate_job_logs("job-1050")
        logs2 = store._generate_job_logs("job-1050")
        assert bodies(logs1) == bodies(logs2)


class TestConcurrency:
    """Test thread safety under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_job_creation(self, store, sample_job_spec):
        """Test creating multiple jobs concurrently."""
        import asyncio

        async def create_job():
            return await store.create_job(sample_job_spec)

        # Create 10 jobs concurrently
        tasks = [create_job() for _ in range(10)]
        jobs = await asyncio.gather(*tasks)

        assert len(jobs) == 10
        job_ids = {job.job_id for job in jobs}
        assert len(job_ids) == 10  # All unique

        # Verify all stored
        for job in jobs:
            stored = await store.get_job(job.job_id)
            assert stored is not None

    @pytest.mark.asyncio
    async def test_concurrent_job_updates(self, store, sample_job_spec):
        """Test updating jobs concurrently."""
        created_job = await store.create_job(sample_job_spec)
        job_id = created_job.job_id

        import asyncio

        async def update_job(field_value):
            return await store.update_job(job_id, status=field_value)

        # Update concurrently with different statuses
        statuses = [
            JobStatus.RUNNING,
            JobStatus.PENDING,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ]
        tasks = [update_job(s) for s in statuses]
        results = await asyncio.gather(*tasks)

        # All updates should succeed (last write wins)
        assert all(r is not None for r in results)

        # Final state should be one of the statuses
        final_job = await store.get_job(job_id)
        assert final_job.status in statuses

    @pytest.mark.asyncio
    async def test_concurrent_node_operations(self, store):
        """Test concurrent node create/get/update/delete."""
        import asyncio

        async def create_and_verify(node_id):
            node = create_node_spec(node_id=node_id)
            await store.create_node(node)
            retrieved = await store.get_node(node_id)
            assert retrieved is not None
            return node_id

        # Create multiple nodes concurrently
        tasks = [create_and_verify(f"concurrent-node-{i}") for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10

        # Verify all exist
        for node_id in results:
            node = await store.get_node(node_id)
            assert node is not None


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_create_job_with_zero_gpus(self, store):
        """Test creating a CPU-only job (0 GPUs)."""
        job_spec = create_job_spec(name="cpu-job", gpus=0, cpus=8, memory_gb=32)
        result = await store.create_job(job_spec)

        assert result.spec.resources.gpus == 0

    @pytest.mark.asyncio
    async def test_create_job_with_many_gpus(self, store):
        """Test creating a job with many GPUs."""
        job_spec = create_job_spec(name="multi-gpu-job", gpus=8, cpus=64, memory_gb=512)
        result = await store.create_job(job_spec)

        assert result.spec.resources.gpus == 8

    @pytest.mark.asyncio
    async def test_create_job_with_custom_env(self, store):
        """Test creating a job with custom environment variables."""
        job_spec = create_job_spec(name="env-job")
        job_spec.env = {
            "CUSTOM_VAR": "value1",
            "ANOTHER_VAR": "value2",
        }
        result = await store.create_job(job_spec)

        assert result.spec.env["CUSTOM_VAR"] == "value1"
        assert result.spec.env["ANOTHER_VAR"] == "value2"

    @pytest.mark.asyncio
    async def test_create_node_with_empty_gpus(self, store):
        """Test creating a node with no GPUs."""
        node = create_node_spec(node_id="cpu-node", gpus=0)
        result = await store.create_node(node)

        assert result.gpus == []

    @pytest.mark.asyncio
    async def test_update_job_with_none_values(self, store, sample_job_spec):
        """Test updating job fields to None."""
        created_job = await store.create_job(sample_job_spec)

        result = await store.update_job(
            created_job.job_id, node_id=None, error=None, exit_code=None
        )

        assert result.node_id is None
        assert result.error is None
        assert result.exit_code is None

    @pytest.mark.asyncio
    async def test_list_jobs_empty_filter_results(self, store, sample_job_spec):
        """Test listing with filters that match nothing."""
        await store.create_job(sample_job_spec)

        # Search for non-existent term
        result, total = await store.list_jobs(search="nonexistent")
        assert total == 0
        assert result == []

        # Filter by non-existent node
        result, total = await store.list_jobs(node_id="non-existent-node")
        assert total == 0
        assert result == []

    @pytest.mark.asyncio
    async def test_large_pagination_offset(self, store, sample_job_spec):
        """Test listing with offset beyond total count."""
        await store.create_job(sample_job_spec)

        result, total = await store.list_jobs(limit=10, offset=1000)
        assert result == []
        assert total >= 11  # At least seed + 1


# Import asyncio at module level for tests that need it
import asyncio
