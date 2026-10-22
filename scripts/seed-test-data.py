#!/usr/bin/env python3
"""
Seed test data for e2e tests.
Runs against the backend API to create jobs, nodes, and profiles.
"""

import asyncio
import os
import sys
import httpx
from datetime import datetime, timezone

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000/api/v1")
SHARED_TOKEN = os.getenv("SHARED_TOKEN", "").strip()

# When running inside the compose stack the shared token is not passed through
# the environment (CI deliberately leaves SHARED_TOKEN unset). Fall back to the
# token persisted by the backend in the shared state volume.
if not SHARED_TOKEN:
    for candidate in ("/app/.shc/shared_token", "/app/.shc/shared-token"):
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                SHARED_TOKEN = fh.read().strip()
                if SHARED_TOKEN:
                    break
        except OSError:
            continue

# Test data definitions - Jobs (7 jobs with varied statuses)
# Note: The status will be set by the backend after creation (PENDING by default)
# The seeding script creates jobs; the backend manages their lifecycle.
JOBS = [
    {
        "name": "gpu-training-run",
        "command": "python train.py --epochs 100",
        "working_dir": "/sync/projects/gpu-training",
        "env": {},
        "resources": {"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 1,
        "retry_delay_seconds": 30,
    },
    {
        "name": "cpu-simulation",
        "command": "mpirun -np 16 simulation",
        "working_dir": "/sync/projects/cpu-sim",
        "env": {},
        "resources": {"gpus": 0, "cpus": 16, "memory_gb": 64, "vram_gb": 0},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 0,
        "retry_delay_seconds": 30,
    },
    {
        "name": "vasp-relaxation",
        "command": "vasp_std > output.log",
        "working_dir": "/sync/projects/vasp-relax",
        "env": {},
        "resources": {"gpus": 1, "cpus": 8, "memory_gb": 32, "vram_gb": 8},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 2,
        "retry_delay_seconds": 60,
    },
    {
        "name": "lammps-md",
        "command": "lmp_mpi -in in.md",
        "working_dir": "/sync/projects/lammps-md",
        "env": {},
        "resources": {"gpus": 0, "cpus": 32, "memory_gb": 64, "vram_gb": 0},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 1,
        "retry_delay_seconds": 30,
    },
    {
        "name": "pending-quantum-espresso",
        "command": "pw.x < input.in",
        "working_dir": "/sync/projects/qe",
        "env": {},
        "resources": {"gpus": 1, "cpus": 4, "memory_gb": 16, "vram_gb": 8},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 0,
        "retry_delay_seconds": 30,
    },
    {
        "name": "cancelled-job",
        "command": "python long_running.py",
        "working_dir": "/sync/projects/cancelled",
        "env": {},
        "resources": {"gpus": 2, "cpus": 8, "memory_gb": 32, "vram_gb": 16},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 0,
        "retry_delay_seconds": 30,
    },
    {
        "name": "gromacs-simulation",
        "command": "gmx_mpi mdrun",
        "working_dir": "/sync/projects/gromacs",
        "env": {},
        "resources": {"gpus": 1, "cpus": 16, "memory_gb": 32, "vram_gb": 8},
        "paths": {"input": "data/in", "output": "data/out"},
        "max_retries": 1,
        "retry_delay_seconds": 60,
    },
]

# Test data definitions - Nodes (4 nodes)
# Note: GPUInfo schema expects name and memory_gb (not memory_total_mb)
NODES = [
    {
        "node_id": "gpu-node-1",
        "hostname": "gpu-node-1",
        "gpus": [
            {"name": "RTX 3090", "memory_gb": 24},
            {"name": "RTX 3090", "memory_gb": 24},
        ],
        "cpus": 32,
        "memory_gb": 128,
        "os": "Linux",
        "status": "ONLINE",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "current_job_id": None,
    },
    {
        "node_id": "gpu-node-2",
        "hostname": "gpu-node-2",
        "gpus": [
            {"name": "A100", "memory_gb": 40},
        ],
        "cpus": 64,
        "memory_gb": 256,
        "os": "Linux",
        "status": "ONLINE",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "current_job_id": None,
    },
    {
        "node_id": "cpu-node-1",
        "hostname": "cpu-node-1",
        "gpus": [],
        "cpus": 64,
        "memory_gb": 512,
        "os": "Linux",
        "status": "ONLINE",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "current_job_id": None,
    },
    {
        "node_id": "offline-node",
        "hostname": "offline-node",
        "gpus": [
            {"name": "V100", "memory_gb": 32},
        ],
        "cpus": 16,
        "memory_gb": 64,
        "os": "Linux",
        "status": "OFFLINE",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "current_job_id": None,
    },
]


async def wait_for_api(base_url: str, timeout: int = 60) -> bool:
    """Wait for the API to be ready."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        for _ in range(timeout):
            try:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "healthy":
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


async def seed_jobs(client: httpx.AsyncClient, headers: dict) -> list:
    """Seed jobs via API."""
    job_ids = []
    for job in JOBS:
        resp = await client.post("/jobs/", json=job, headers=headers)
        if resp.status_code == 201:
            job_data = resp.json()
            job_ids.append(job_data["job_id"])
            print(f"Created job: {job['name']} ({job_data['job_id']})")
        elif resp.status_code == 409:
            # Job might already exist, try to get its ID
            print(f"Job {job['name']} already exists (409), skipping")
        else:
            print(f"Failed to create job {job['name']}: {resp.status_code} {resp.text}")
            return []
    return job_ids


async def seed_nodes(client: httpx.AsyncClient, headers: dict) -> list:
    """Seed nodes via API."""
    node_ids = []
    for node in NODES:
        resp = await client.post("/nodes/", json=node, headers=headers)
        if resp.status_code == 201:
            node_data = resp.json()
            node_ids.append(node_data["node_id"])
            print(f"Created node: {node['node_id']} ({node_data['node_id']})")
        elif resp.status_code == 409:
            # Node might already exist
            print(f"Node {node['node_id']} already exists (409), skipping")
        else:
            print(
                f"Failed to create node {node['node_id']}: "
                f"{resp.status_code} {resp.text}"
            )
            return []
    return node_ids


async def main():
    print("Waiting for API to be ready...")
    if not await wait_for_api(BASE_URL):
        print("ERROR: API did not become ready in time")
        sys.exit(1)

    print("API is ready. Seeding test data...")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # On localhost bypass hosts the backend accepts the magic token directly.
        # Elsewhere (e.g. the compose stack, where the request originates from the
        # db-seed container and the host is "backend"), exchange the shared token
        # for a JWT access token first.
        if SHARED_TOKEN:
            try:
                token_resp = await client.post(
                    "/auth/token", json={"shared_token": SHARED_TOKEN}
                )
                if token_resp.status_code == 200:
                    access_token = token_resp.json().get("access_token")
                else:
                    print(
                        "Failed to mint access token:",
                        token_resp.status_code,
                        token_resp.text,
                    )
                    access_token = None
            except Exception as exc:  # pragma: no cover - network failure
                print(f"Failed to mint access token: {exc}")
                access_token = None
            if not access_token:
                print("ERROR: could not obtain an access token for seeding")
                sys.exit(1)
            headers = {"Authorization": f"Bearer {access_token}"}
        else:
            headers = {"Authorization": "Bearer localhost-no-auth"}

        # Seed nodes first (jobs may reference nodes)
        await seed_nodes(client, headers)

        # Seed jobs
        await seed_jobs(client, headers)

    print("Test data seeding completed!")


if __name__ == "__main__":
    asyncio.run(main())
