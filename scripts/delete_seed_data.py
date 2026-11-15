#!/usr/bin/env python3
"""Delete demo/e2e seed rows from a Scientific Home Cluster SQLite database.

Removes:
  - backend internal demo seed: job-1041..job-1050, node-alpha..node-delta
  - db-seed (seed-test-data.py) rows: jobs named gpu-training-run, cpu-simulation,
    vasp-relaxation, lammps-md, pending-quantum-espresso, cancelled-job,
    gromacs-simulation; nodes gpu-node-1, gpu-node-2, cpu-node-1, offline-node
"""

import argparse
import os
import sqlite3
import subprocess
import sys

DEMO_JOB_RANGE = ("job-1041", "job-1050")
DEMO_NODE_IDS = ["node-alpha", "node-beta", "node-gamma", "node-delta"]

E2E_JOB_NAMES = [
    "gpu-training-run",
    "cpu-simulation",
    "vasp-relaxation",
    "lammps-md",
    "pending-quantum-espresso",
    "cancelled-job",
    "gromacs-simulation",
]
E2E_NODE_IDS = ["gpu-node-1", "gpu-node-2", "cpu-node-1", "offline-node"]


def _delete_jobs(conn, job_ids):
    for job_id in job_ids:
        for table in ("gpu_metrics", "cpu_metrics", "log_entries"):
            conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
    if job_ids:
        placeholders = ",".join("?" for _ in job_ids)
        conn.execute(f"DELETE FROM jobs WHERE job_id IN ({placeholders})", job_ids)


def delete_seed_data(db_path: str) -> None:
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("BEGIN;")
        conn.execute("UPDATE nodes SET current_job_id = NULL;")

        # Backend internal demo seed (job-1041..job-1050)
        conn.execute(
            "DELETE FROM gpu_metrics WHERE job_id BETWEEN 'job-1041' AND 'job-1050';"
        )
        conn.execute(
            "DELETE FROM cpu_metrics WHERE job_id BETWEEN 'job-1041' AND 'job-1050';"
        )
        conn.execute(
            "DELETE FROM log_entries WHERE job_id BETWEEN 'job-1041' AND 'job-1050';"
        )
        conn.execute("DELETE FROM jobs WHERE job_id BETWEEN 'job-1041' AND 'job-1050';")
        conn.execute(
            "DELETE FROM nodes WHERE node_id IN ('node-alpha','node-beta',"
            "'node-gamma','node-delta');"
        )

        # db-seed (seed-test-data.py) rows
        placeholders = ",".join("?" for _ in E2E_JOB_NAMES)
        query = (
            "SELECT job_id FROM jobs WHERE json_extract(spec, '$.name') "
            f"IN ({placeholders})"
        )
        rows = conn.execute(query, E2E_JOB_NAMES).fetchall()
        _delete_jobs(conn, [r[0] for r in rows])
        if E2E_NODE_IDS:
            placeholders = ",".join("?" for _ in E2E_NODE_IDS)
            conn.execute(
                f"DELETE FROM nodes WHERE node_id IN ({placeholders})", E2E_NODE_IDS
            )

        conn.commit()
        problems = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if problems:
            print("Foreign key violations:", problems, file=sys.stderr)
            sys.exit(1)
        print("Seed data deleted.")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete seed data from the SHC database."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("SHC_DB", "data/scientific_home_cluster.db"),
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Run the cleanup in the Docker stack (running or down).",
    )
    args = parser.parse_args()

    if args.docker:
        script = open(__file__, encoding="utf-8").read()
        # Running container
        try:
            subprocess.run(
                ["docker", "compose", "exec", "-T", "backend", "python", "-"],
                input=script,
                text=True,
                check=True,
            )
            return
        except subprocess.CalledProcessError:
            pass
        # Downed container: helper with volume mounted
        env = dict(os.environ)
        env["SHC_DB"] = "/data/scientific_home_cluster.db"
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "-v",
                "shc-data:/data",
                "python:3.11-slim",
                "python",
                "-",
            ],
            input=script,
            text=True,
            check=True,
            env=env,
        )
    else:
        delete_seed_data(args.db)


if __name__ == "__main__":
    main()
