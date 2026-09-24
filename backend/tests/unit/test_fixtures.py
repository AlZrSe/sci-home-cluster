"""
Test to verify the new conftest fixtures work correctly.
"""

import pytest
from httpx import AsyncClient
from backend.store.memory import InMemoryStore
from backend.models.job_spec import JobSpec
from backend.models.node_spec import NodeSpec
from backend.tests.factories import (
    JobSpecFactory,
    NodeSpecFactory,
    GPUInfoFactory,
    create_job_spec,
    create_node_spec,
)


@pytest.mark.asyncio
async def test_client_fixture(client: AsyncClient):
    """Test that the client fixture works."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "Welcome to Scientific Home Cluster API" in response.json()["message"]


@pytest.mark.asyncio
async def test_auth_client_fixture(auth_client: AsyncClient):
    """Test that the auth_client fixture provides authenticated client."""
    # The auth_client should have the localhost bypass token
    assert "Authorization" in auth_client.headers
    assert auth_client.headers["Authorization"] == "Bearer localhost-no-auth"

    # Should be able to access protected endpoints
    response = await auth_client.get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_sample_job_fixture(sample_job: JobSpec):
    """Test that the sample_job fixture provides a valid JobSpec."""
    assert isinstance(sample_job, JobSpec)
    assert sample_job.name == "test-job"
    assert sample_job.command == "python train.py --epochs 10"
    assert sample_job.resources.gpus == 1
    assert sample_job.resources.cpus == 4
    assert sample_job.resources.memory_gb == 16


@pytest.mark.asyncio
async def test_sample_node_fixture(sample_node: NodeSpec):
    """Test that the sample_node fixture provides a valid NodeSpec."""
    assert isinstance(sample_node, NodeSpec)
    assert sample_node.node_id == "test-node-01"
    assert sample_node.hostname == "test-node-01.lan"
    assert len(sample_node.gpus) == 1
    assert sample_node.gpus[0].name == "NVIDIA RTX 4090"
    assert sample_node.cpus == 16
    assert sample_node.memory_gb == 64
    assert sample_node.status == "ONLINE"


@pytest.mark.asyncio
async def test_mock_store_fixture(mock_store: InMemoryStore):
    """Test that the mock_store fixture provides an isolated InMemoryStore."""
    assert isinstance(mock_store, InMemoryStore)

    # Store should be empty (no seed data since it's a fresh instance)
    nodes = await mock_store.list_nodes()
    jobs, total = await mock_store.list_jobs()

    # Fresh instance should have no seed data
    assert len(nodes) == 0
    assert total == 0

    # But we can create data
    from backend.tests.factories import create_node_spec, create_job_spec

    node = create_node_spec()
    await mock_store.create_node(node)

    job = create_job_spec()
    created_job = await mock_store.create_job(job)

    nodes = await mock_store.list_nodes()
    jobs, total = await mock_store.list_jobs()

    assert len(nodes) == 1
    assert total == 1
    assert created_job.job_id is not None


@pytest.mark.asyncio
async def test_syncthing_root_fixture(syncthing_root: str):
    """Test that the syncthing_root fixture provides a temporary directory."""
    import os

    assert isinstance(syncthing_root, str)
    assert os.path.exists(syncthing_root)
    assert os.path.isdir(syncthing_root)

    # Should be able to write to it
    test_file = os.path.join(syncthing_root, "test.txt")
    with open(test_file, "w") as f:
        f.write("test")

    assert os.path.exists(test_file)


@pytest.mark.asyncio
async def test_db_session_fixture(db_session):
    """Test that the db_session fixture provides an async SQLAlchemy session."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import text

    assert isinstance(db_session, AsyncSession)

    # Test we can execute a query
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1

    # Test we can execute another query
    result = await db_session.execute(text("SELECT 'hello' as msg"))
    assert result.scalar() == "hello"


def test_jobspec_factory():
    """Test that JobSpecFactory generates valid instances."""
    job = JobSpecFactory()
    assert isinstance(job, JobSpec)
    assert job.name.startswith("test-job-")
    assert len(job.command) >= 3
    assert job.resources.gpus >= 0
    assert job.resources.cpus >= 1
    assert job.resources.memory_gb >= 1


def test_nodespec_factory():
    """Test that NodeSpecFactory generates valid instances."""
    node = NodeSpecFactory()
    assert isinstance(node, NodeSpec)
    assert node.node_id.startswith("node-")
    assert node.hostname.endswith(".lan")
    assert isinstance(node.gpus, list)
    assert node.cpus >= 1
    assert node.memory_gb >= 1
    assert node.status in ["ONLINE", "OFFLINE"]


def test_gpuinfo_factory():
    """Test that GPUInfoFactory generates valid instances."""
    gpu = GPUInfoFactory()
    assert gpu.name.startswith("NVIDIA RTX ")
    assert gpu.memory_gb in [8, 12, 16, 24, 36, 48]


def test_create_job_spec():
    """Test the create_job_spec convenience function."""
    job = create_job_spec(name="my-custom-job", gpus=2, cpus=8, memory_gb=32)
    assert job.name == "my-custom-job"
    assert job.resources.gpus == 2
    assert job.resources.cpus == 8
    assert job.resources.memory_gb == 32


def test_create_node_spec():
    """Test the create_node_spec convenience function."""
    node = create_node_spec(node_id="custom-node", gpus=4, status="OFFLINE")
    assert node.node_id == "custom-node"
    assert len(node.gpus) == 4
    assert node.status == "OFFLINE"
