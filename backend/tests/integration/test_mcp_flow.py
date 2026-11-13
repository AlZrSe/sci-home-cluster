"""Integration tests for MCP SSE/HTTP transport."""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app


@pytest.fixture
async def async_client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        c.headers["Authorization"] = "Bearer localhost-no-auth"
        yield c


@pytest.mark.asyncio
async def test_mcp_unauthenticated_returns_401(monkeypatch):
    import backend.core.config as cfg

    monkeypatch.setattr(cfg.settings, "LOCALHOST_BYPASS", False)
    monkeypatch.setattr(cfg.settings, "LOCALHOST_BYPASS_MODE", None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        r = await c.post(
            "/mcp/messages",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_mcp_initialize_and_tools_list(async_client):
    r = await async_client.post(
        "/mcp/messages",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert r.status_code == 200
    assert r.json()["result"]["protocolVersion"] == "2024-11-05"

    r2 = await async_client.post(
        "/mcp/messages",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert r2.status_code == 200
    tools = [t["name"] for t in r2.json()["result"]["tools"]]
    assert "jobs.create" in tools
    assert "cluster.health" in tools


@pytest.mark.asyncio
async def test_mcp_job_create_get_list(async_client):
    spec = {
        "name": "mcp-job",
        "command": "echo hi",
        "working_dir": "/tmp",
        "resources": {"gpus": 0, "cpus": 1, "memory_gb": 1},
    }
    r = await async_client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "jobs.create", "arguments": {"spec": spec}},
        },
    )
    assert r.status_code == 200
    job = r.json()["result"]["structuredContent"]
    job_id = job["job_id"]

    r2 = await async_client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "jobs.get", "arguments": {"job_id": job_id}},
        },
    )
    assert r2.json()["result"]["structuredContent"]["job_id"] == job_id

    r3 = await async_client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "jobs.list", "arguments": {}},
        },
    )
    assert r3.json()["result"]["structuredContent"]["total"] >= 1


@pytest.mark.asyncio
async def test_mcp_resources_and_prompts(async_client):
    r = await async_client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "cluster://status"},
        },
    )
    assert r.status_code == 200

    r2 = await async_client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "prompts/get",
            "params": {"name": "analyze_results", "arguments": {"job_id": "job-1"}},
        },
    )
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_mcp_readonly_blocks_mutation(async_client, monkeypatch):
    import backend.core.config as cfg

    monkeypatch.setattr(cfg.settings, "MCP_READ_ONLY", True)
    spec = {
        "name": "ro-job",
        "command": "echo hi",
        "working_dir": "/tmp",
        "resources": {"gpus": 0, "cpus": 1, "memory_gb": 1},
    }
    r = await async_client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "jobs.create", "arguments": {"spec": spec}},
        },
    )
    assert r.json()["result"]["isError"] is True
    assert "read_only_mode" in r.json()["result"]["content"][0]["text"]
