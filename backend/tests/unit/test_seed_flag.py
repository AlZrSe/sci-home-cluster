"""
Tests for the SEED_DEMO_DATA gate.

Demo seeding used to run unconditionally, so a production database was
populated with 10 fake jobs and 4 fake nodes by the first request. These
tests pin the two halves of the fix: the flag really does keep an empty
database empty, and turning it back on reproduces the old dataset exactly.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

import pytest
from sqlalchemy import func, select

from backend.core.config import Settings, settings
from backend.core.database import get_session
from backend.store import DatabaseStore
from backend.store.database import JobModel, NodeModel
from backend.tests.factories import create_job_spec
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def row_counts() -> Tuple[int, int]:
    """Direct (jobs, nodes) row counts, bypassing the API and the store."""
    async with get_session() as session:
        jobs = await session.execute(select(func.count()).select_from(JobModel))
        nodes = await session.execute(select(func.count()).select_from(NodeModel))
        return jobs.scalar_one(), nodes.scalar_one()


def job_state(job_id: str) -> JobState:
    """A minimal valid JobState with an explicit id."""
    return JobState(
        job_id=job_id,
        spec=create_job_spec(name="pre-existing"),
        status=JobStatus.PENDING,
        created_at=datetime.now(),
        retry_count=0,
    )


def preloaded_store() -> DatabaseStore:
    """
    A store that writes rows without ever running the seeder.

    Models "this database already contains these ids, written by something
    else". The restart scenarios have to insert their pre-existing rows
    before any store call reaches _ensure_seeded(), otherwise the seeder
    (with the flag on) would write job-1050 itself and the insert below
    would collide with it.
    """
    store = DatabaseStore()
    store._seeded = True
    return store


async def wipe_tables() -> None:
    """Empty every table without re-seeding."""
    from backend.tests.conftest import _clear_store_data

    await _clear_store_data(DatabaseStore())


@pytest.fixture
def flag_off(monkeypatch):
    """Force SEED_DEMO_DATA off for one test."""
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)


@pytest.fixture
def flag_on(monkeypatch):
    """Force SEED_DEMO_DATA on for one test (the test-session default)."""
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)


@pytest.fixture
async def store_off(clean_database, flag_off) -> DatabaseStore:
    """A fresh DatabaseStore against empty tables, with seeding disabled."""
    return DatabaseStore()


@pytest.fixture
async def store_on(clean_database, flag_on) -> DatabaseStore:
    """A fresh DatabaseStore against empty tables, with seeding enabled."""
    return DatabaseStore()


# ---------------------------------------------------------------------------
# U1 / U2 / AC-1: the setting itself
# ---------------------------------------------------------------------------


def test_default_is_off(tmp_path, monkeypatch):
    """AC-1: with nothing set anywhere, demo seeding is off."""
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    assert Settings(_env_file=str(empty_env)).SEED_DEMO_DATA is False


@pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on"])
def test_flag_parses_truthy_from_env_file(tmp_path, raw):
    """U2: truthy values parse to True via an env file."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"SEED_DEMO_DATA={raw}\n", encoding="utf-8")
    os.environ.pop("SEED_DEMO_DATA", None)

    assert Settings(_env_file=str(env_file)).SEED_DEMO_DATA is True


@pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off"])
def test_flag_parses_falsy_from_env_file(tmp_path, raw):
    """U2: falsy values parse to False via an env file."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"SEED_DEMO_DATA={raw}\n", encoding="utf-8")
    os.environ.pop("SEED_DEMO_DATA", None)

    assert Settings(_env_file=str(env_file)).SEED_DEMO_DATA is False


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False)],
)
def test_flag_parses_from_process_environment(tmp_path, monkeypatch, raw, expected):
    """U2: the same values parse when supplied through os.environ."""
    monkeypatch.setenv("SEED_DEMO_DATA", raw)
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    assert Settings(_env_file=str(empty_env)).SEED_DEMO_DATA is expected


# ---------------------------------------------------------------------------
# U3 / U4: AC-2, AC-3, AC-4 at the store level
# ---------------------------------------------------------------------------


async def test_store_stays_empty_when_flag_off(store_off):
    """
    AC-2/AC-3: no read path inserts a row when seeding is disabled.

    Exercises every _ensure_seeded() call site reachable without writing,
    then checks the raw tables rather than trusting the API response.
    """
    store = store_off

    jobs, total = await store.list_jobs()
    assert jobs == []
    assert total == 0

    assert await store.list_nodes() == []
    assert await store.get_job("job-1050") is None
    assert await store.get_node("node-alpha") is None
    assert await store.get_job_metrics("job-1050") is None
    assert await store.get_node_metrics("node-alpha") is None

    # update/delete on absent ids are no-ops and must not create anything.
    assert await store.update_job("job-1050", status=JobStatus.RUNNING) is None
    assert await store.delete_job("job-1050") is False
    assert await store.update_node("node-alpha", status="ONLINE") is None
    assert await store.delete_node("node-alpha") is False

    # get_job_logs synthesises lines for unknown ids by design (a separate
    # issue), so it is excluded here - but it must not create a job row.
    await store.get_job_logs("job-1050")

    assert await row_counts() == (0, 0)


async def test_writes_create_exactly_one_row_when_flag_off(store_off, clean_database):
    """
    AC-3: the write paths add their own row and nothing else.

    A partial fix that gated only the read paths would still let
    create_job/create_node pull in the whole seed dataset.
    """
    store = store_off

    created = await store.create_job(create_job_spec(name="only-job"))
    assert await row_counts() == (1, 0)

    from shared.schemas.node_spec import GPUInfo, NodeSpec

    await store.create_node(
        NodeSpec(
            node_id="only-node",
            hostname="only-node.lan",
            gpus=[GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)],
            cpus=4,
            memory_gb=16,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime.now(),
        )
    )

    jobs, total = await store.list_jobs()
    assert total == 1
    assert jobs[0].job_id == created.job_id
    assert len(await store.list_nodes()) == 1
    assert await row_counts() == (1, 1)


async def test_store_seeds_when_flag_on(store_on):
    """AC-4 negative control: the flag on reproduces today's dataset."""
    store = store_on

    await store.list_nodes()

    nodes = await store.list_nodes()
    assert {n.node_id for n in nodes} == {
        "node-alpha",
        "node-beta",
        "node-gamma",
        "node-delta",
    }

    jobs, total = await store.list_jobs()
    assert total == 10
    assert {j.job_id for j in jobs} == {f"job-{1050 - i}" for i in range(10)}

    # The distinctive values from the frontend mock server.
    # The FAILED job is index 5 of the seed list, i.e. job-1045.
    failed = await store.get_job("job-1045")
    assert failed.status == JobStatus.FAILED
    assert failed.exit_code == 137
    assert failed.error == "CUDA out of memory at step 12841"
    assert failed.retry_count == 2

    assert (await store.get_job("job-1050")).spec.name == "protein-fold-batch"
    assert store._job_counter == 1050


async def test_seeding_is_byte_identical_between_two_instances(
    store_on, clean_database
):
    """
    AC-4: two fresh stores produce the same rows.

    Guards the opt-in path against a partial edit to _seed_data.
    """

    async def snapshot() -> dict:
        instance = DatabaseStore()
        jobs, _ = await instance.list_jobs()
        nodes = await instance.list_nodes()
        return {
            "jobs": sorted(
                (j.job_id, j.spec.name, j.status.value, j.exit_code, j.error)
                for j in jobs
            ),
            "nodes": sorted(
                (n.node_id, n.hostname, n.status, n.cpus, n.memory_gb) for n in nodes
            ),
        }

    first = await snapshot()
    await wipe_tables()
    second = await snapshot()

    assert first == second
    assert len(first["jobs"]) == 10
    assert len(first["nodes"]) == 4


# ---------------------------------------------------------------------------
# U5 / U6: AC-5, AC-6, AC-3 - the job-counter trap
# ---------------------------------------------------------------------------


async def test_job_counter_syncs_when_seeding_disabled(store_off, clean_database):
    """
    AC-5: after a restart the next id is above every id already stored.

    A restart is modelled by building a new DatabaseStore - _seeded and
    _job_counter are per-instance, so the new instance re-runs
    _ensure_seeded(). Skipping _sync_job_counter() when the flag is off
    leaves the counter at 0 and the next job is issued as `job-1`.
    """
    await preloaded_store().create_job_with_id(job_state("job-1050"))

    restarted = DatabaseStore()
    assert restarted._job_counter == 0

    # First request after the restart triggers the lazy startup path.
    _, total = await restarted.list_jobs()
    assert total == 1
    assert restarted._job_counter == 1050

    created = await restarted.create_job(create_job_spec(name="after-restart"))
    assert created.job_id == "job-1051"


async def test_job_counter_syncs_when_seeding_enabled(store_on, clean_database):
    """
    AC-5 negative control: same scenario with the flag on.

    The seeder's own `job_count > 0` guard means it declines to add
    anything, so this reproduces today's behaviour exactly.
    """
    await preloaded_store().create_job_with_id(job_state("job-1050"))

    restarted = DatabaseStore()
    await restarted.list_jobs()
    assert restarted._job_counter == 1050

    created = await restarted.create_job(create_job_spec(name="after-restart"))
    assert created.job_id == "job-1051"


async def test_no_id_collision_with_existing_low_ids(store_off, clean_database):
    """
    AC-6: low-numbered Syncthing-synced ids must not collide.

    Jobs arriving via the folder scan get ids like job-1 and job-2. If the
    counter sync is skipped, the next created job is job-1, which raises
    IntegrityError on the job_id primary key and surfaces as HTTP 500.
    """
    preloaded = preloaded_store()
    await preloaded.create_job_with_id(job_state("job-1"))
    await preloaded.create_job_with_id(job_state("job-2"))

    restarted = DatabaseStore()
    created = await restarted.create_job(create_job_spec(name="synced-cluster"))

    assert int(created.job_id.split("-")[1]) > 2
    assert created.job_id == "job-3"
    assert await restarted.get_job("job-1") is not None
    assert await restarted.get_job("job-2") is not None


async def test_no_id_collision_with_existing_low_ids_when_seeding_enabled(
    store_on, clean_database
):
    """AC-6 negative control: same scenario with the flag on."""
    preloaded = preloaded_store()
    await preloaded.create_job_with_id(job_state("job-1"))
    await preloaded.create_job_with_id(job_state("job-2"))

    restarted = DatabaseStore()
    created = await restarted.create_job(create_job_spec(name="synced-cluster"))
    assert int(created.job_id.split("-")[1]) > 2


async def test_counter_starts_at_one_on_a_virgin_database(store_off):
    """
    AC-6 / R7: job-1 is correct when there is nothing to collide with.

    Pins the behaviour so nobody "fixes" the counter by pre-seeding it with
    a constant - that would reintroduce the collision above.
    """
    store = store_off
    created = await store.create_job(create_job_spec(name="first-ever"))
    assert created.job_id == "job-1"
    assert store._job_counter == 1


# ---------------------------------------------------------------------------
# U7 / AC-7: reset() must respect the flag
# ---------------------------------------------------------------------------


async def test_reset_does_not_reseed_when_flag_off(store_off, clean_database):
    """
    AC-7: with the flag off, reset() empties the tables and they stay empty.

    reset() calls _seed_data() directly, bypassing _ensure_seeded(), so
    gating only _ensure_seeded would leave this seeding unconditionally -
    and the autouse reset_singleton_store fixture runs it before every test.
    """
    store = store_off
    await store.create_job(create_job_spec(name="will-be-wiped"))
    assert (await row_counts())[0] == 1

    await store.reset()

    assert await row_counts() == (0, 0)
    assert await store.list_jobs() == ([], 0)
    assert await store.list_nodes() == []
    assert store._seeded is True
    assert store._job_counter == 0


async def test_reset_reseeds_when_flag_on(store_on, clean_database):
    """AC-7: with the flag on, reset() restores the dataset as before."""
    store = store_on
    await store.reset()

    nodes = await store.list_nodes()
    assert len(nodes) >= 4
    jobs, total = await store.list_jobs()
    assert total >= 10
    assert store._job_counter == 1050


# ---------------------------------------------------------------------------
# AC-11: an existing database is never rewritten
# ---------------------------------------------------------------------------


async def test_existing_rows_are_left_alone_when_flag_off(store_off, clean_database):
    """
    AC-11: switching the flag off destroys and renumbers nothing.

    The seed rows live in the same tables as real rows, so the store must
    not try to distinguish or clean them up.
    """
    await preloaded_store().create_job_with_id(job_state("job-42"))
    before = await row_counts()
    assert before == (1, 0)

    store = store_off
    await store.list_jobs()

    assert await row_counts() == before
    assert (await store.get_job("job-42")) is not None


def test_repo_has_no_dotenv_example():
    """
    C5/OQ-5: documentation lives in the README, not a new dotfile.

    The issue asked for the flag to be documented in .env.example, but no
    such file exists in this repo and adding one is out of scope.
    """
    assert not Path(".env.example").exists()
