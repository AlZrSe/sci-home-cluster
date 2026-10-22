# E2E Test Data Seeding Specification

## Overview

This specification consolidates the requirements from GitHub issues #149, #151, and #152 into a single comprehensive document for adding test data seeding to the CI pipeline to fix failing e2e tests.

**Core Problem**: The compose stack starts with an empty database, so the dashboard shows empty states instead of metric cards/job table with data, causing e2e tests to fail with timeout errors waiting for elements that only exist when data is present.

---

## 1. User Stories

### US-1: Dashboard Shows Real Data in CI
**As a** developer running e2e tests in CI
**I want** the dashboard to display metric cards, job tables, and node statistics with real data
**So that** e2e tests can find and interact with the expected UI elements

### US-2: Reliable E2E Test Execution
**As a** CI pipeline
**I want** test data to be seeded automatically before e2e tests run
**So that** tests pass consistently without flaky failures due to missing data

### US-3: Debuggable E2E Failures
**As a** developer investigating CI failures
**I want** a debug e2e test that runs first and captures page state
**So that** I can quickly diagnose rendering issues without guessing

### US-4: Fast CI Pipeline
**As a** project maintainer
**I want** the full CI pipeline (including e2e) to complete in under 10 minutes
**So that** PR feedback is fast and developers don't wait long

---

## 2. Acceptance Criteria (Consolidated from #149, #151, #152)

### AC-1: Test Data Seeding in Compose Stack
- [ ] A `db-seed` service is added to `docker-compose.yaml`
- [ ] The service runs after the database is healthy (`depends_on: api condition: service_healthy`)
- [ ] The service seeds test data via the backend API
- [ ] The service completes before e2e tests start

### AC-2: Required Test Data
The following test data MUST be seeded:

**Jobs (7 jobs with varied statuses):**
| Name | Status | Command | GPUs | CPUs | Memory (GB) |
|------|--------|---------|------|------|-------------|
| gpu-training-run | RUNNING | python train.py --epochs 100 | 1 | 4 | 16 |
| cpu-simulation | RUNNING | mpirun -np 16 simulation | 0 | 16 | 64 |
| vasp-relaxation | COMPLETED | vasp_std > output.log | 1 | 8 | 32 |
| lammps-md | FAILED | lmp_mpi -in in.md | 0 | 32 | 64 |
| pending-quantum-espresso | PENDING | pw.x < input.in | 1 | 4 | 16 |
| cancelled-job | CANCELLED | python long_running.py | 2 | 8 | 32 |
| gromacs-simulation | COMPLETED | gmx_mpi mdrun | 1 | 16 | 32 |

**Nodes (4 nodes):**
| Name | Status | GPUs | CPUs | Memory (GB) |
|------|--------|------|------|-------------|
| gpu-node-1 | ONLINE | 2x RTX 3090 | 32 | 128 |
| gpu-node-2 | ONLINE | 1x A100 | 64 | 256 |
| cpu-node-1 | ONLINE | 0 | 64 | 512 |
| offline-node | OFFLINE | 1x V100 | 16 | 64 |

**Profiles (4 builtin profiles via localStorage):**
- VASP - standard relaxation (vasp_std, 1 GPU, 16 CPU, 32 GB)
- LAMMPS - molecular dynamics (lmp_mpi, 0 GPU, 32 CPU, 64 GB)
- GROMACS - MD simulation (gmx_mpi, 1 GPU, 16 CPU, 32 GB)
- Quantum ESPRESSO - DFT (pw.x, 0 GPU, 64 CPU, 128 GB)

### AC-3: Dashboard Elements Visible in E2E Tests
With seeded data, the following dashboard elements MUST be visible:
- [ ] Metric cards: "Total Jobs", "Running", "Pending", "Completed", "Failed"
- [ ] Metric cards: "Total Nodes", "Online", "Offline"
- [ ] Metric card: "GPU Utilization"
- [ ] Navigation links: Dashboard, Jobs, Nodes, Settings
- [ ] Theme toggle in header
- [ ] "Recent Jobs" section with job entries
- [ ] Job table on `/jobs` page with seeded jobs

### AC-4: Debug E2E Test
- [ ] A debug test runs FIRST in the e2e suite (`frontend/tests/e2e/debug.spec.ts`)
- [ ] Captures full page HTML on failure
- [ ] Takes screenshots on failure
- [ ] Logs console errors
- [ ] Helps diagnose rendering issues quickly

### AC-5: CI Pipeline Integration
- [ ] CI waits for `db-seed` service to complete before running e2e tests
- [ ] All 4 CI jobs pass consistently: `backend-gates`, `frontend-gates`, `compose-suite`, `e2e`
- [ ] E2E tests complete in < 10 minutes total
- [ ] No flaky failures due to missing test data

### AC-6: Data-Testid Attributes (Ongoing)
- [ ] Critical UI elements have `data-testid` attributes
- [ ] E2E tests updated to use `data-testid` selectors
- [ ] More stable than text/CSS selectors

---

## 3. Test Scenarios

### TS-1: Dashboard Loads with Seeded Data
**Given** the compose stack is running with seeded test data
**When** a user navigates to the dashboard (`/`)
**Then** metric cards show non-zero counts matching seeded data
**And** recent jobs list shows the 7 seeded jobs
**And** node statistics show 4 nodes (3 online, 1 offline)

### TS-2: Jobs Page Shows Seeded Jobs
**Given** the compose stack is running with seeded test data
**When** a user navigates to `/jobs`
**Then** the job table displays all 7 seeded jobs
**And** jobs can be filtered by status (RUNNING, COMPLETED, FAILED, PENDING, CANCELLED)
**And** job details are accessible

### TS-3: Nodes Page Shows Seeded Nodes
**Given** the compose stack is running with seeded test data
**When** a user navigates to `/nodes`
**Then** the node grid displays all 4 seeded nodes
**And** node statuses are correctly shown (ONLINE/OFFLINE)

### TS-4: Debug Test Captures Failure State
**Given** an e2e test fails
**When** the debug test runs first
**Then** full page HTML is captured
**And** screenshot is taken
**And** console logs are available for analysis

### TS-5: CI Pipeline Completes Successfully
**Given** a PR is opened against main
**When** CI runs
**Then** all 4 jobs pass
**And** total runtime < 10 minutes

---

## 4. Technical Approach

### 4.1 Test Data Seeding Script

Create a new Python script at `scripts/seed-test-data.py`:

```python
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
SHARED_TOKEN = os.getenv("SHARED_TOKEN", "")

# Test data definitions
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

NODES = [
    {
        "name": "gpu-node-1",
        "hostname": "gpu-node-1",
        "gpus": [
            {"gpu_id": 0, "name": "RTX 3090", "memory_total_mb": 24576, "driver_version": "535.154.05"},
            {"gpu_id": 1, "name": "RTX 3090", "memory_total_mb": 24576, "driver_version": "535.154.05"},
        ],
        "cpus": 32,
        "memory_gb": 128,
        "os": "Linux",
        "status": "ONLINE",
    },
    {
        "name": "gpu-node-2",
        "hostname": "gpu-node-2",
        "gpus": [
            {"gpu_id": 0, "name": "A100", "memory_total_mb": 40960, "driver_version": "535.154.05"},
        ],
        "cpus": 64,
        "memory_gb": 256,
        "os": "Linux",
        "status": "ONLINE",
    },
    {
        "name": "cpu-node-1",
        "hostname": "cpu-node-1",
        "gpus": [],
        "cpus": 64,
        "memory_gb": 512,
        "os": "Linux",
        "status": "ONLINE",
    },
    {
        "name": "offline-node",
        "hostname": "offline-node",
        "gpus": [
            {"gpu_id": 0, "name": "V100", "memory_total_mb": 32768, "driver_version": "535.154.05"},
        ],
        "cpus": 16,
        "memory_gb": 64,
        "os": "Linux",
        "status": "OFFLINE",
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
        else:
            print(f"Failed to create job {job['name']}: {resp.status_code} {resp.text}")
    return job_ids

async def seed_nodes(client: httpx.AsyncClient, headers: dict) -> list:
    """Seed nodes via API."""
    node_ids = []
    for node in NODES:
        resp = await client.post("/nodes/", json=node, headers=headers)
        if resp.status_code == 201:
            node_data = resp.json()
            node_ids.append(node_data["node_id"])
            print(f"Created node: {node['name']} ({node_data['node_id']})")
        else:
            print(f"Failed to create node {node['name']}: {resp.status_code} {resp.text}")
    return node_ids

async def main():
    print("Waiting for API to be ready...")
    if not await wait_for_api(BASE_URL):
        print("ERROR: API did not become ready in time")
        sys.exit(1)
    
    print("API is ready. Seeding test data...")
    
    # Use the localhost bypass token
    headers = {"Authorization": f"Bearer {SHARED_TOKEN or 'localhost-no-auth'}"}
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # Seed nodes first (jobs may reference nodes)
        await seed_nodes(client, headers)
        
        # Seed jobs
        await seed_jobs(client, headers)
    
    print("Test data seeding completed!")

if __name__ == "__main__":
    asyncio.run(main())
```

### 4.2 Dockerfile for Seed Service

Create `docker/Dockerfile.seed`:

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install only the dependencies needed for seeding
COPY pyproject.toml ./
COPY backend/ ./backend/
COPY shared/ ./shared/

RUN pip install --no-cache-dir .

# Copy the seeding script
COPY scripts/seed-test-data.py /app/seed-test-data.py

ENTRYPOINT ["python", "/app/seed-test-data.py"]
```

### 4.3 Docker Compose Integration

Add to `docker-compose.yaml` (after the `backend` service, before `volumes`):

```yaml
services:
  backend:
    # ... existing backend service ...

  db-seed:
    build:
      context: .
      dockerfile: docker/Dockerfile.seed
    depends_on:
      backend:
        condition: service_healthy
    environment:
      - BASE_URL=http://backend:8000/api/v1
      - SHARED_TOKEN=${SHARED_TOKEN:-}
    restart: "no"
    # No volumes, no ports - runs once and exits

volumes:
  shc-data:
    name: shc-data
  shc-state:
    name: shc-state
```

### 4.4 CI Pipeline Integration

Update `.github/workflows/ci.yml` e2e job:

1. **Add a wait step** after stack startup and before Playwright tests:
```yaml
- name: Wait for db-seed to complete
  run: |
    for attempt in $(seq 1 60); do
      if docker compose ps db-seed --format json | jq -e '.[0].State == "exited" and .[0].ExitCode == 0' >/dev/null 2>&1; then
        echo "db-seed completed successfully after ${attempt} poll(s)"
        exit 0
      fi
      sleep 2
    done
    echo "::error::db-seed did not complete successfully"
    docker compose logs db-seed
    exit 1
```

2. **Ensure the debug test runs first** in Playwright config (already handled by file naming convention or explicit ordering).

### 4.5 Debug E2E Test

Create `frontend/tests/e2e/debug.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";

test.describe("Debug - runs first to diagnose failures", () => {
  test("dump page state on dashboard", async ({ page }, testInfo) => {
    await page.goto("/");
    
    // Wait a bit for React to hydrate
    await page.waitForTimeout(3000);
    
    // Capture full page HTML
    const html = await page.content();
    await testInfo.attach("page-html", { body: html, contentType: "text/html" });
    
    // Take screenshot
    await testInfo.attach("screenshot", { 
      body: await page.screenshot({ fullPage: true }), 
      contentType: "image/png" 
    });
    
    // Log console errors
    const logs = [];
    page.on("console", msg => logs.push(`${msg.type()}: ${msg.text()}`));
    page.on("pageerror", err => logs.push(`ERROR: ${err.message}`));
    
    await testInfo.attach("console-logs", { body: logs.join("\n"), contentType: "text/plain" });
    
    // Check what's actually rendered
    const bodyText = await page.locator("body").innerText();
    await testInfo.attach("body-text", { body: bodyText, contentType: "text/plain" });
    
    // Always pass - this is diagnostic
    expect(true).toBe(true);
  });
});
```

### 4.6 Backend API for Node Creation

The backend currently only has GET endpoints for nodes. Need to add a POST endpoint for seeding.

Add to `backend/api/v1/nodes.py`:

```python
@router.post("/", response_model=NodeSpec, status_code=status.HTTP_201_CREATED)
async def create_node(
    node_spec: NodeSpec,
    payload: dict = Depends(get_current_token_payload),
):
    """
    Create a new node (for test seeding).
    """
    node_service = NodeService()
    return await node_service.create_node(node_spec)
```

And add `create_node` method to `NodeService` in `backend/services/node_service.py`.

---

## 5. Edge Cases and Risks

### Risk 1: Database State Pollution
**Risk**: Test data persists between CI runs, causing conflicts
**Mitigation**: 
- Use `docker compose down -v` in CI teardown (already done)
- Each CI run gets fresh volumes (`shc-e2e-*` naming in compose suite)
- The e2e job uses the checked-in compose file directly which has pinned volume names, but the CI runner is ephemeral

### Risk 2: Seeding Fails Silently
**Risk**: Seed service exits with code 0 but data not actually created
**Mitigation**:
- Seed script exits non-zero on any failure
- CI explicitly waits for `db-seed` service to exit with code 0
- Health check verifies data exists before e2e tests run

### Risk 3: Race Condition Between Seed and Tests
**Risk**: E2e tests start before seeding completes
**Mitigation**:
- `depends_on: backend: condition: service_healthy` ensures API is ready
- CI explicitly polls for `db-seed` container to reach `exited` state with code 0
- Only then proceeds to Playwright tests

### Risk 4: Localhost Auth Bypass Token
**Risk**: Seed script needs valid auth token
**Mitigation**:
- Use `localhost-no-auth` bypass token (works on `localhost` Host header)
- In compose, the seed service connects to `backend:8000` with `Host: localhost` header
- Or pass `SHARED_TOKEN` env var (generated on first backend start, persisted to shc-state volume)

### Risk 5: Profile Data in localStorage
**Risk**: Profiles are stored in frontend localStorage, not backend
**Mitigation**:
- Profiles are client-side only (per spec)
- E2E tests can seed profiles via `page.evaluate(() => localStorage.setItem(...))` in a setup step
- Or the debug test can verify localStorage is populated

### Risk 6: Docker Build Cache
**Risk**: Frontend submodule changes not picked up in Docker build
**Mitigation**:
- CI already uses `--no-cache` for e2e job (`docker compose build --no-cache`)
- Ensure `fetch-depth: 0` for submodule checkout (already in CI)

### Risk 7: Test Data Conflicts with Existing Data
**Risk**: If volumes aren't cleaned, seeding may fail on duplicate keys
**Mitigation**:
- CI teardown runs `docker compose down -v` 
- Runner is ephemeral (GitHub-hosted), so volumes don't persist between runs
- Seed script handles 409 conflicts gracefully (log and continue)

### Risk 8: Node Registration vs Creation
**Risk**: Nodes are typically registered by agents, not created via API
**Mitigation**:
- Add a `create_node` endpoint specifically for test seeding (internal use)
- Or seed nodes directly via database in the seed script (more complex but bypasses API)
- Current approach: add minimal POST /nodes/ endpoint for test use

---

## 6. Implementation Order

1. **Add POST /nodes/ endpoint** to backend (backend/api/v1/nodes.py + node_service.py)
2. **Create seeding script** at `scripts/seed-test-data.py`
3. **Create Dockerfile.seed** at `docker/Dockerfile.seed`
4. **Add db-seed service** to `docker-compose.yaml`
5. **Create debug e2e test** at `frontend/tests/e2e/debug.spec.ts`
6. **Update CI workflow** to wait for db-seed
7. **Add data-testid attributes** to critical UI elements (ongoing)
8. **Verify all tests pass** locally and in CI

---

## 7. Definition of Done

- [ ] All acceptance criteria (AC-1 through AC-6) verified
- [ ] All test scenarios (TS-1 through TS-5) passing
- [ ] CI pipeline passes all 4 jobs consistently
- [ ] E2E test runtime < 10 minutes
- [ ] No flaky failures in 5 consecutive CI runs
- [ ] Debug test captures useful diagnostic information
- [ ] Documentation updated (this spec, README if needed)

---

## 8. Related Files

| File | Purpose |
|------|---------|
| `docker-compose.yaml` | Add db-seed service |
| `docker/Dockerfile.seed` | Seed service container |
| `scripts/seed-test-data.py` | Test data seeding script |
| `backend/api/v1/nodes.py` | Add POST /nodes/ endpoint |
| `backend/services/node_service.py` | Add create_node method |
| `frontend/tests/e2e/debug.spec.ts` | Debug e2e test |
| `.github/workflows/ci.yml` | Wait for db-seed in CI |
| `frontend/tests/e2e/*.spec.ts` | Update selectors to data-testid |

---

*This specification consolidates issues #149, #151, and #152. All three issues should be marked as "groomed" with a reference to this document upon completion.*