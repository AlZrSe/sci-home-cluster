"""
Unit tests for the in-memory store.
"""

import asyncio
import pytest
from datetime import datetime
from backend.store.memory import InMemoryStore
from backend.models.node_spec import NodeSpec, GPUInfo
from backend.models.job_spec import JobSpec


@pytest.fixture
def store():
    """Create a fresh store instance for each test."""
    return InMemoryStore()


@pytest.fixture
def sample_node():
    """Create a sample node spec for testing."""
    return NodeSpec(
        node_id="test-node",
        hostname="test.lan",
        gpus=[GPUInfo(name="Test GPU", memory_gb=8)],
        cpus=4,
        memory_gb=16,
        os="TestOS",
        status="ONLINE",
        last_heartbeat=datetime.now(),
        current_job_id=None
    )


@pytest.fixture
def sample_job_spec():
    """Create a sample job spec for testing."""
    return JobSpec(
        name="test-job",
        command="echo hello",
        working_dir="/tmp",
        env={},
        resources={
            "gpus": 1,
            "cpus": 2,
            "memory_gb": 4,
            "vram_gb": 2
        },
        paths={"input": "/tmp/in", "output": "/tmp/out"},
        retry={"max_retries": 3, "retry_delay_seconds": 60}
    )


class TestNodeCRUD:
    """Test node CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_node(self, store, sample_node):
        """Test creating a new node."""
        # Act
        result = await store.create_node(sample_node)
        
        # Assert
        assert result.node_id == sample_node.node_id
        assert result.hostname == sample_node.hostname
        assert len(result.gpus) == len(sample_node.gpus)
        assert result.gpus[0].name == sample_node.gpus[0].name
        assert result.cpus == sample_node.cpus
        assert result.memory_gb == sample_node.memory_gb
        assert result.os == sample_node.os
        assert result.status == sample_node.status
        
        # Verify it's stored
        stored_node = await store.get_node(sample_node.node_id)
        assert stored_node is not None
        assert stored_node.node_id == sample_node.node_id

    @pytest.mark.asyncio
    async def test_create_node_duplicate(self, store, sample_node):
        """Test creating a node that already exists raises an error."""
        # Arrange
        await store.create_node(sample_node)
        
        # Act & Assert
        with pytest.raises(ValueError, match="already exists"):
            await store.create_node(sample_node)

    @pytest.mark.asyncio
    async def test_get_node(self, store, sample_node):
        """Test getting a node by ID."""
        # Arrange
        await store.create_node(sample_node)
        
        # Act
        result = await store.get_node(sample_node.node_id)
        
        # Assert
        assert result is not None
        assert result.node_id == sample_node.node_id
        assert result.hostname == sample_node.hostname

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, store):
        """Test getting a non-existent node returns None."""
        # Act
        result = await store.get_node("non-existent")
        
        # Assert
        assert result is None



    @pytest.mark.asyncio
    async def test_update_node(self, store, sample_node):
        """Test updating a node's fields."""
        # Arrange
        await store.create_node(sample_node)
        
        # Act
        result = await store.update_node(
            sample_node.node_id,
            hostname="updated.lan",
            status="OFFLINE"
        )
        
        # Assert
        assert result is not None
        assert result.hostname == "updated.lan"
        assert result.status == "OFFLINE"
        # Other fields should remain unchanged
        assert result.node_id == sample_node.node_id
        assert result.cpus == sample_node.cpus

    @pytest.mark.asyncio
    async def test_update_node_not_found(self, store):
        """Test updating a non-existent node returns None."""
        # Act
        result = await store.update_node("non-existent", hostname="test.lan")
        
        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_node(self, store, sample_node):
        """Test deleting a node."""
        # Arrange
        await store.create_node(sample_node)
        
        # Act
        result = await store.delete_node(sample_node.node_id)
        
        # Assert
        assert result is True
        
        # Verify it's gone
        stored_node = await store.get_node(sample_node.node_id)
        assert stored_node is None

    @pytest.mark.asyncio
    async def test_delete_node_not_found(self, store):
        """Test deleting a non-existent node returns False."""
        # Act
        result = await store.delete_node("non-existent")
        
        # Assert
        assert result is False


class TestJobCRUD:
    """Test job CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_job(self, store, sample_job_spec):
        """Test creating a new job."""
        # Act
        result = await store.create_job(sample_job_spec)
        
        # Assert
        assert result.job_id is not None
        assert result.job_id.startswith("job-")
        assert result.spec.name == sample_job_spec.name
        assert result.spec.command == sample_job_spec.command
        assert result.status == "PENDING"  # JobStatus.PENDING
        assert result.retry_count == 0
        assert result.created_at is not None
        
        # Verify it's stored
        stored_job = await store.get_job(result.job_id)
        assert stored_job is not None
        assert stored_job.job_id == result.job_id

    @pytest.mark.asyncio
    async def test_create_job(self, store, sample_job_spec):
        """Test creating a new job."""
        # Act
        result = await store.create_job(sample_job_spec)
        
        # Assert
        assert result.job_id is not None
        assert result.job_id.startswith("job-")
        assert result.spec.name == sample_job_spec.name
        assert result.spec.command == sample_job_spec.command
        assert result.status == "PENDING"  # JobStatus.PENDING
        assert result.retry_count == 0
        assert result.created_at is not None
        
        # Verify it's stored
        stored_job = await store.get_job(result.job_id)
        assert stored_job is not None
        assert stored_job.job_id == result.job_id

    @pytest.mark.asyncio
    async def test_get_job(self, store, sample_job_spec):
        """Test getting a job by ID."""
        # Arrange
        created_job = await store.create_job(sample_job_spec)
        
        # Act
        result = await store.get_job(created_job.job_id)
        
        # Assert
        assert result is not None
        assert result.job_id == created_job.job_id
        assert result.spec.name == sample_job_spec.name

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, store):
        """Test getting a non-existent job returns None."""
        # Act
        result = await store.get_job("non-existent")
        
        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_list_jobs(self, store, sample_job_spec):
        """Test listing jobs with filtering."""
        # Arrange
        job1 = await store.create_job(sample_job_spec)
        job2_spec = sample_job_spec.model_copy(update={"name": "test-job-2"})
        job2 = await store.create_job(job2_spec)
        
        # Act
        result, total = await store.list_jobs()
        
        # Assert
        assert total == 12  # 10 seed jobs + 2 test jobs
        assert len(result) == 12
        job_ids = {node.node_id for node in result}
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(self, store, sample_job_spec):
        """Test listing jobs filtered by status."""
        # Arrange
        job1 = await store.create_job(sample_job_spec)
        job2 = await store.create_job(sample_job_spec)
        
        # Update one job to RUNNING status
        await store.update_job(job1.job_id, status="RUNNING")
    
    # Act
    running_jobs, running_total = await store.list_jobs(status="RUNNING")
    pending_jobs, pending_total = await store.list_jobs(status="PENDING")
    
    # Assert
    assert running_total == 4  # 3 seed RUNNING + 1 job1 set to RUNNING
    assert len(running_jobs) == 4
    assert running_jobs[0].job_id == job1.job_id
    
    assert pending_total == 3  # 2 seed PENDING + 1 job2 (still PENDING)
assert len(pending_jobs) == 3
        assert pending_jobs[0].job_id == job2.job_id



    @pytest.mark.asyncio
    async def test_update_job(self, store, sample_job_spec):
        """Test updating a job's fields."""
        # Arrange
        created_job = await store.create_job(sample_job_spec)
        
        # Act
        result = await store.update_job(
            created_job.job_id,
            status="RUNNING",
            node_id="test-node"
        )
        
        # Assert
        assert result is not None
        assert result.status == "RUNNING"
        assert result.node_id == "test-node"
        # Other fields should remain unchanged
        assert result.job_id == created_job.job_id
assert result.spec.name == sample_job_spec.name

    @pytest.mark.asyncio
    async def test_update_job_not_found(self, store):
        """Test updating a non-existent job returns None."""
        # Act
        result = await store.update_job("non-existent", status="RUNNING")
        
        # Assert
assert result is None

    @pytest.mark.asyncio
    async def test_delete_job(self, store, sample_job_spec):
        """Test deleting a job."""
        # Arrange
        created_job = await store.create_job(sample_job_spec)
        
        # Act
        result = await store.delete_job(created_job.job_id)
        
        # Assert
        assert result is True
        
        # Verify it's gone
        stored_job = await store.get_job(created_job.job_id)
assert stored_job is None

    @pytest.mark.asyncio
    async def test_delete_job_not_found(self, store):
        """Test deleting a non-existent job returns False."""
        # Act
        result = await store.delete_job("non-existent")
        
        # Assert
        assert result is False


class TestStoreReset:
    """Test store reset functionality."""

    @pytest.mark.asyncio
    async def test_reset(self, store):
        """Test resetting the store to initial state."""
        # Arrange
        # Add some data
        node = NodeSpec(
            node_id="test-node",
            hostname="test.lan",
            gpus=[GPUInfo(name="Test GPU", memory_gb=8)],
            cpus=4,
            memory_gb=16,
            os="TestOS",
            status="ONLINE",
            last_heartbeat=datetime.now(),
            current_job_id=None
        )
        await store.create_node(node)
        
        job_spec = JobSpec(
            name="test-job",
            command="echo hello",
            working_dir="/tmp",
            env={},
            resources={
                "gpus": 1,
                "cpus": 2,
                "memory_gb": 4,
                "vram_gb": 2
            },
            paths={"input": "/tmp/in", "output": "/tmp/out"},
            retry={"max_retries": 3, "retry_delay_seconds": 60}
        )
        job = await store.create_job(job_spec)
        
        # Verify data exists
        assert await store.get_node("test-node") is not None
        assert await store.get_job(job.job_id) is not None
        
        # Act
        await store.reset()
        
        # Assert
        # Data should be cleared
        assert await store.get_node("test-node") is None
        assert await store.get_job(job.job_id) is None
        
        # But seed data should be present
        nodes = await store.list_nodes()
        assert len(nodes) > 0  # Should have seed nodes
        
        jobs, total = await store.list_jobs()
        assert total > 0  # Should have seed jobs
        
        # Job counter should be reset to seed value
        # After seeding, the counter should be set to the highest seed job ID
        # From our seed data, we have jobs job-1050 down to job-1041, so counter should be 1050
        assert store._job_counter == 1050


class TestSeedData:
    """Test that seed data matches expectations."""

    @pytest.mark.asyncio
    async def test_seed_nodes_exist(self, store):
        """Test that expected seed nodes are present."""
        # Act
        nodes = await store.list_nodes()
        
        # Assert
        node_ids = {node.node_id for node in nodes}
        expected_nodes = {"node-alpha", "node-beta", "node-gamma", "node-delta"}
        assert expected_nodes.issubset(node_ids)
        
        # Check specific node details
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
        # Act
        jobs, total = await store.list_jobs()
        
        # Assert
        assert total >= 10  # At least the 10 seed jobs
        
        # Check for specific seed job IDs from our seed data
        expected_job_ids = {f"job-{1050 - i}" for i in range(10)}
        actual_job_ids = {job.job_id for job in jobs}
        assert expected_job_ids.issubset(actual_job_ids)
        
        # Check a specific job
        job_1050 = await store.get_job("job-1050")
        assert job_1050 is not None
        assert job_1050.spec.name == "protein-fold-batch"
        assert job_1050.status == "RUNNING"  # First status in our list
        assert job_1050.node_id is not None  # Should be assigned to a node

    @pytest.mark.asyncio
    async def test_seed_data_deterministic(self):
        """Test that seed data is deterministic across store instances."""
        # Arrange
        store1 = InMemoryStore()
        store2 = InMemoryStore()
        
        # Act
        nodes1 = await store1.list_nodes()
        nodes2 = await store2.list_nodes()
        jobs1, _ = await store1.list_jobs()
        jobs2, _ = await store2.list_jobs()
        
        # Assert
        # Node data should be identical
        assert len(nodes1) == len(nodes2)
        for n1, n2 in zip(sorted(nodes1, key=lambda n: n.node_id),
                          sorted(nodes2, key=lambda n: n.node_id)):
            assert n1.node_id == n2.node_id
            assert n1.hostname == n2.hostname
            assert n1.status == n2.status
            assert n1.cpus == n2.cpus
            assert n1.memory_gb == n2.memory_gb
            
        # Job data should be identical
        assert len(jobs1) == len(jobs2)
        for j1, j2 in zip(sorted(jobs1, key=lambda j: j.job_id),
                          sorted(jobs2, key=lambda j: j.job_id)):
            assert j1.job_id == j2.job_id
            assert j1.spec.name == j2.spec.name
            assert j1.status == j2.status
            # Note: node_id might differ due to random assignment, but should be from same set