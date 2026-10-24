# Test Data Seeding for e2e Tests

## Problem
The e2e tests fail because the dashboard page in the CI environment shows empty states instead of the expected metric cards and job table. This is because the compose stack starts with an empty database.

## Current State
- Database starts empty in compose stack
- Dashboard shows "No jobs match these filters" instead of job table
- Metric cards show "0" or "�?" instead of real counts
- e2e tests can't find elements that only exist when data exists

## Solution: Add Test Data Seeding

### Required Test Data
1. **Jobs** (5-10 jobs with varied statuses):
   - 2 RUNNING jobs
   - 2 COMPLETED jobs  
   - 1 FAILED job
   - 1 PENDING job
   - 1 CANCELLED job
   - Various resource requirements (GPUs, CPUs, memory)

2. **Nodes** (3-4 nodes):
   - 2 ONLINE nodes with GPU resources
   - 1 OFFLINE node
   - 1 node with no GPUs

3. **Profiles** (builtin + 1 custom):
   - VASP - standard relaxation
   - LAMMPS - molecular dynamics
   - GROMACS - MD simulation
   - Quantum ESPRESSO - DFT

### Implementation Approach

#### Option 1: Database Seeding Script (Recommended)
Create a Python script that:
1. Connects to the database
2. Inserts test data directly via SQLAlchemy
3. Runs as a compose service before e2e tests

#### Option 2: API-Based Seeding (Current Approach)
Use the backend API to create test data:
1. POST /api/v1/jobs to create jobs
2. POST /api/v1/nodes to create nodes
3. POST /api/v1/profiles to create profiles

### Implementation Plan

#### 1. Create Seeding Script
Create `scripts/seed-test-data.py`:
```python
#!/usr/bin/env python3
"""Seed test data for e2e tests."""
import asyncio
import httpx
import os

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000/api/v1")
TOKEN = os.getenv("SEED_TOKEN", "test-seed-token")

async def seed_data():
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        
        # Create profiles
        profiles = [...]
        for p in profiles:
            await client.post("/profiles", json=p, headers=headers)
        
        # Create nodes
        nodes = [...]
        for n in nodes:
            await client.post("/nodes", json=n, headers=headers)
        
        # Create jobs
        jobs = [...]
        for j in jobs:
            await client.post("/jobs", json=j, headers=headers)

if __name__ == "__main__":
    asyncio.run(seed_data())
```

#### 2. Add to Docker Compose
Add to `docker-compose.yaml`:
```yaml
services:
  db-seed:
    build:
      context: .
      dockerfile: docker/Dockerfile.seed
    depends_on:
      api:
        condition: service_healthy
    environment:
      - BASE_URL=http://api:8000/api/v1
      - SEED_TOKEN=${SHARED_TOKEN}
    restart: "no"
```

#### 3. Add to CI Pipeline
In `.github/workflows/ci.yml`:
- Add step to wait for db-seed completion before e2e tests
- Or add seeding step in backend tests compose fixture

### Test Data Specifications

#### Jobs
| Name | Status | Command | GPUs | CPUs | Memory |
|------|--------|---------|------|------|--------|
| gpu-training | RUNNING | python train.py | 1 | 4 | 16 |
| cpu-simulation | RUNNING | mpirun sim | 0 | 16 | 64 |
| completed-job | COMPLETED | python done.py | 1 | 8 | 32 |
| failed-job | FAILED | python fail.py | 2 | 8 | 32 |
| pending-job | PENDING | python wait.py | 1 | 4 | 16 |

#### Nodes
| Name | Status | GPUs | CPUs | Memory |
|------|--------|------|------|--------|
| gpu-node-1 | ONLINE | 2x RTX 3090 | 32 | 128 |
| gpu-node-2 | ONLINE | 1x A100 | 64 | 256 |
| cpu-node-1 | ONLINE | 0 | 64 | 512 |
| offline-node | OFFLINE | 1x V100 | 16 | 64 |

#### Profiles
| Name | Command | GPUs | CPUs | Memory |
|------|---------|------|------|--------|
| VASP | vasp_std | 1 | 16 | 32 |
| LAMMPS | lmp_mpi | 0 | 32 | 64 |
| GROMACS | gmx_mpi | 1 | 16 | 32 |
| QE | pw.x | 0 | 64 | 128 |

## Implementation Files
- `scripts/seed-test-data.py` - New seeding script
- `docker/Dockerfile.seed` - New Dockerfile for seed service
- `docker-compose.yaml` - Add db-seed service
- `backend/tests/compose/conftest.py` - Add seeding fixture
- `.github/workflows/ci.yml` - Wait for seeding in CI

## Success Criteria
- e2e tests find dashboard elements (metric cards, job table, navigation)
- Dashboard shows real data instead of empty states
- All 4 CI jobs pass consistently
- Test data is isolated and doesn't affect production data