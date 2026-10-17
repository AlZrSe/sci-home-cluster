"""
MUST M-4.1 .. M-4.3 - what is actually mounted, and what actually crosses the
bind mount (layer L4).

**MUST M-4.2 is the single most important scenario in this suite.** It is the
only assertion anywhere here that distinguishes "the Syncthing bind mount
works" from "the backend made itself an empty folder and reported `available`".

Unmounted, the backend creates `/tmp/syncthing/{jobs,nodes}` itself
(`backend/services/syncthing_service.py:190-193`) and then reports
`syncthing_status: "available"` because the directory it just created exists.
That is issue #104's silent second cluster: the cluster looks healthy, jobs get
submitted, and no node ever claims one. `M-3.2`'s `syncthing.status ==
"available"` assertion passes in exactly that broken case, so it must never be
cited as evidence the mount works. M-4.2 is the evidence.
"""

import os
import sys
import time
from datetime import datetime

import pytest

from shared.file_ops.yaml_utils import read_yaml
from shared.schemas.job_state import JobState
from shared.schemas.node_spec import NodeSpec

from backend.tests.compose.conftest import (
    COMPOSE_PYTESTMARK,
    ClusterClient,
    ComposeStack,
    submit_job,
)

pytestmark = COMPOSE_PYTESTMARK

#: The code trees that must NOT be bind-mounted. A mount over any of them would
#: make the container's behaviour depend on the host checkout - the class of bug
#: this repository has been bitten by twice (issues #34 and #58, both CWD-relative
#: path resolution). `docker-compose.yaml:222-232` states the rule and names the
#: cost.
UNMOUNTED_TREES = (
    "/app/backend",
    "/app/frontend",
    "/app/shared",
    "/app/agent",
    "/app/cli",
)


# ---------------------------------------------------------------------------
# M-4.1  Exactly the three documented mounts, nothing over the code
# ---------------------------------------------------------------------------


def test_exactly_the_three_documented_mounts(compose_stack: ComposeStack) -> None:
    """
    M-4.1 - `docker inspect <cid>.Mounts` has exactly three entries, with the
    destinations and types of AC-6, and none is mounted over `/app/backend`,
    `/app/frontend`, `/app/shared`, `/app/agent` or `/app/cli`.

    Protects `docker-compose.yaml:222-232`: "Three mounts, and the count is the
    point. Nothing under /app/frontend, /app/backend, /app/shared, /app/agent or
    /app/cli is mounted - those are image content, and a bind mount over any of
    them would make the container's behaviour depend on the host checkout, the
    class of bug this repository has been bitten by twice (issues #34 and #58)."

    Read from `docker inspect` rather than from the resolved config, because
    this is the question about the RUNNING container. The config is asserted
    separately by MUST M-1.7; a mismatch between the two would be exactly the
    class of defect this layer exists to catch.
    """
    mounts = compose_stack.inspect("{{json .Mounts}}")
    assert len(mounts) == 3, (
        f"the running container has {len(mounts)} mounts: "
        f"{[(m['Type'], m['Source'], m['Destination']) for m in mounts]}"
    )

    by_destination = {mount["Destination"]: mount for mount in mounts}
    assert set(by_destination) == {
        "/app/data",
        "/app/.shc",
        "/tmp/syncthing",
    }, f"mount destinations are {sorted(by_destination)}"

    assert by_destination["/app/data"]["Type"] == "volume", by_destination["/app/data"]
    assert by_destination["/app/.shc"]["Type"] == "volume", by_destination["/app/.shc"]
    assert by_destination["/tmp/syncthing"]["Type"] == "bind", (
        f"{by_destination['/tmp/syncthing']} - a named volume there would be "
        "invisible to every agent on every other node, which is the entire point "
        "of SYNCTHING_ROOT (docker-compose.yaml:234-245)"
    )

    for tree in UNMOUNTED_TREES:
        offenders = [
            mount["Source"]
            for mount in mounts
            if mount["Destination"] == tree
            or mount["Destination"].startswith(tree.rstrip("/") + "/")
        ]
        assert offenders == [], (
            f"{offenders} is mounted over {tree}. That makes the container's "
            "behaviour depend on the host checkout (docker-compose.yaml:222-232; "
            "issues #34 and #58)."
        )


# ---------------------------------------------------------------------------
# M-4.2  A job submitted over the port appears ON THE HOST
# ---------------------------------------------------------------------------


def test_a_submitted_job_appears_on_the_host(compose_stack: ComposeStack) -> None:
    """
    M-4.2 - a job submitted over the published port shows up at
    ``<bind-source>/jobs/<job_id>/state.yaml`` **on the host**, where
    ``<bind-source>`` is read from the resolved config and never hardcoded. The
    file exists, parses as a `JobState`, and its ``job_id`` matches what the API
    returned.

    Protects `backend/services/job_service.py:75-78`,
    `backend/services/job_folder.py:61-86` and `:125-129` (`write_job_state` ->
    ``<root>/jobs/<id>/state.yaml``), the `Observer` scheduled on `jobs_dir` at
    `backend/services/syncthing_service.py:202-205`, and
    `docker-compose.yaml:234-245`.

    **This is the scenario that kills #104's silent second cluster.** Everything
    else in the suite passes when the bind mount is absent: the container reports
    `syncthing_status: "available"` because it created the directory itself, the
    database has a job row, and the API answers. What fails is the file appearing
    where the fleet can see it.

    Two non-vacuity properties make the difference:

    * the bind source comes from the RUNNING stack's own resolved config, so a
      regression that moved the mount is caught rather than papered over by a
      hardcoded path;
    * the file is parsed with the repository's own `read_yaml` + `JobState`, so a
      truncated or shape-wrong document fails here rather than being counted as
      "the file exists".
    """
    bind_source = compose_stack.bind_source()
    assert bind_source.is_absolute(), bind_source

    job_id = submit_job(compose_stack)

    state_file = compose_stack.job_state_file(job_id)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not state_file.is_file():
        time.sleep(0.5)

    assert state_file.is_file(), (
        f"no {state_file} on the host 60 s after POST /api/v1/jobs/ returned "
        f"{job_id}. The bind source is {bind_source}, read from the running "
        "stack's resolved config.\n"
        "Either the bind mount is not in effect - in which case the backend "
        "created /tmp/syncthing itself "
        "(backend/services/syncthing_service.py:190-193) and reported "
        "`syncthing_status: available` anyway, which is issue #104's silent "
        "second cluster - or the folder write did not happen "
        "(backend/services/job_service.py:93-99)."
    )

    state = read_yaml(str(state_file), JobState)
    assert state.job_id == job_id, (
        f"{state_file} carries job_id={state.job_id!r}, the API returned "
        f"{job_id!r}. The directory name is the primary key "
        "(backend/services/job_folder.py:66-74); a mismatch means one job's "
        "state is being written into another's directory."
    )
    assert state.spec.name == "compose-e2e", state.spec

    # D1: "the guard did not fire" is worth confirming rather than assuming.
    # `assert_no_db_in_source_tree` watches the whole repo for new *.db, and the
    # bind source is INSIDE the repo. The suite writes YAML there, never a
    # database, and this makes that a checked property.
    stray_databases = sorted(str(p) for p in bind_source.rglob("*.db"))
    assert stray_databases == [], (
        f"the compose run created SQLite files under the bind source: "
        f"{stray_databases}. The suite writes state.yaml there, never a database."
    )


# ---------------------------------------------------------------------------
# M-4.3  The database lives on shc-data, and the pool is null
# ---------------------------------------------------------------------------


def test_database_is_on_the_volume_and_the_pool_is_null(
    compose_stack: ComposeStack, cluster: ClusterClient
) -> None:
    """
    M-4.3 - `docker compose exec backend printenv DB_POOL` prints `null`;
    `health.database.url` ends `/app/data/scientific_home_cluster.db`; and that
    file is present inside `/app/data`.

    Protects `docker/Dockerfile:128`, `docker-compose.yaml:183-220` ("To verify
    what the container actually runs") and `backend/core/database.py:108`.

    The `printenv` form is the one `docker-compose.yaml:219-220` documents, so
    this is that instruction executed rather than described. The three
    assertions together answer "which database am I on and with what pool",
    which is issue #34's question and the reason `/health` reports the absolute
    path (`backend/main.py:362-367`).
    """
    printed = compose_stack.exec("printenv", "DB_POOL")
    assert printed.stdout.strip() == "null", (
        f"the container runs DB_POOL={printed.stdout.strip()!r}, not 'null'. "
        "The image bakes DB_POOL=null (docker/Dockerfile:128) and compose "
        "inherits it deliberately (docker-compose.yaml:122-134). Any other value "
        "than 'null' selects a bare QueuePool on an async engine, which "
        "SQLAlchemy 2.0 rejects - see MUST M-5.5."
    )

    health = cluster.get("/api/v1/health").json()
    assert health["database"]["url"].endswith(
        "/app/data/scientific_home_cluster.db"
    ), health

    listing = compose_stack.exec("ls", "-A", "/app/data").stdout.split()
    assert "scientific_home_cluster.db" in listing, (
        f"/app/data holds {listing}. The database has to be on the shc-data "
        "volume mounted at /app/data (docker-compose.yaml:231) or a container "
        "replace destroys the cluster."
    )


# ---------------------------------------------------------------------------
# SHOULD S-1  A file dropped on the host is seen by the container's watcher
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "host -> container inotify events do not propagate reliably through "
        "Docker Desktop's gRPC-FUSE / osxfs file share on macOS and Windows, so "
        "the `watchdog` Observer scheduled inside the container may never fire. "
        "Gated on a NATIVE Linux bind mount rather than on a timeout: a test that "
        "is flaky on the platform half the team uses will be muted, and then it "
        "protects nothing. The container -> host direction (M-4.2) is reliable "
        "everywhere and is what the suite relies on."
    ),
)
def test_a_node_dropped_on_the_host_is_seen_by_the_container(
    compose_stack: ComposeStack, cluster: ClusterClient
) -> None:
    """
    SHOULD S-1 - write a valid `NodeSpec` at `<bind-source>/nodes/<node_id>.yaml`
    on the host and poll `GET /api/v1/nodes` until it appears.

    Protects `backend/services/syncthing_service.py:202-205` (the `Observer`
    scheduled on `nodes_dir`), `:243-265` (`_initial_scan`) and `:336-340`, and
    `shared/schemas/node_spec.py:15-28`.

    **Written atomically - temp file then `os.replace`** - because
    `backend/services/job_folder.py:110-113` records that a partially-written
    document is dropped by BOTH watchers with no visible failure. A plain
    `write_text` here would produce a flaky test for a reason that has nothing to
    do with the bind mount.

    This is the one direction in the suite that is not reliable on every
    platform; see the `skipif` above.
    """
    node_id = "compose-e2e-node"
    nodes_dir = compose_stack.bind_source() / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)

    spec = NodeSpec(
        node_id=node_id,
        hostname="compose-e2e.invalid",
        gpus=[],
        cpus=1,
        memory_gb=1,
        os="Linux",
        status="ONLINE",
        last_heartbeat=datetime.now(),
    )
    destination = nodes_dir / f"{node_id}.yaml"
    temporary = nodes_dir / f".{node_id}.yaml.tmp"
    temporary.write_text(spec.model_dump_json(), encoding="utf-8")
    os.replace(temporary, destination)

    deadline = time.monotonic() + 60
    seen = False
    while time.monotonic() < deadline and not seen:
        listed = cluster.get("/api/v1/nodes").json()
        seen = any(node["node_id"] == node_id for node in listed)
        if not seen:
            time.sleep(1.0)

    assert seen, (
        f"{destination} was written on the host and did not appear in "
        "GET /api/v1/nodes within 60 s. Either the inotify event did not cross "
        "the bind mount, or the watcher is not reading the directory the mount "
        "points at (backend/services/syncthing_service.py:202-205)."
    )
    # Tidy up after the one file this test wrote, by exact name.
    destination.unlink(missing_ok=True)
