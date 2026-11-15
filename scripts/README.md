# Scripts

Helper scripts for running and maintaining the Scientific Home Cluster.

## Docker Compose runner

Run the stack in different configurations without starting the `db-seed` service and
without seeding demo data (`SEED_DEMO_DATA=false`).

Bash:
```bash
./scripts/docker-run.sh dev
./scripts/docker-run.sh prod
./scripts/docker-run.sh localhost
```

PowerShell:
```powershell
.\scripts\docker-run.ps1 -Mode dev
.\scripts\docker-run.ps1 -Mode prod
.\scripts\docker-run.ps1 -Mode localhost
```

Production mode requires `VITE_API_URL` (and ideally `SHARED_TOKEN`) in `.env` or the
environment.

## Delete demo seed data

Remove the demo seed rows (`job-1041`..`job-1050`, `node-alpha`..`node-delta`) from
the database.

Bash:
```bash
./scripts/delete-seed-data.sh
./scripts/delete-seed-data.sh --db path/to/scientific_home_cluster.db
./scripts/delete-seed-data.sh --docker
```

PowerShell:
```powershell
.\scripts\delete-seed-data.ps1
.\scripts\delete-seed-data.ps1 -Db path\to\scientific_home_cluster.db
.\scripts\delete-seed-data.ps1 -Docker
```

Direct Python:
```bash
python scripts/delete_seed_data.py --db data/scientific_home_cluster.db
python scripts/delete_seed_data.py --docker
```