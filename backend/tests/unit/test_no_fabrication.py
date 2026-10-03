"""
Tests for the read-time fabrication gate (issue #35).

Until this issue the store invented metrics and log lines whenever a read
found nothing, and - worse - persisted the invention. Five call sites did
it: get_job_metrics, get_node_metrics, both branches of get_job_logs, and
_stream_worker, which invented a line AND committed it every 1.4 seconds
for any RUNNING job without anybody reading anything.

These tests pin the replacement contract:

  * 404  <=> the resource does not exist  (never gated)
  * empty <=> it exists and nothing has been collected yet

and, above all, that reading does not write. The assertions are on raw
table row counts, not on HTTP responses, because "the endpoint returned
200" is not the property that regressed - the rows appearing in the table
did.

The session-wide default is SEED_DEMO_DATA=True (conftest.py), so most of
the existing suite exercises demo mode. Everything here therefore uses the
flag_off / store_off / seeding_disabled fixtures explicitly.
"""

import asyncio

from sqlalchemy import func, select

from backend.core.config import settings
from backend.core.database import get_session
from backend.services.job_service import JobService
from backend.store import DatabaseStore
from backend.store.database import (
    CPUMetricModel,
    GPUMetricModel,
    LogEntryModel,
)
from backend.tests.factories import create_job_spec, create_node_spec
from backend.tests.unit.test_seed_flag import job_state, preloaded_store
from shared.schemas.job_status import JobStatus

# One worker tick is 1.4s. Three ticks is the shortest wait that can show
# "the worker is not running" rather than "the worker has not got round to
# it yet". A fifth of a tick is enough to prove one tick did NOT happen.
TICK = 1.4
THREE_TICKS = 3 * TICK + 0.5

SUMMARY_FIELDS = (
    "gpu_memory_min_mb",
    "gpu_memory_max_mb",
    "gpu_memory_avg_mb",
    "gpu_util_min",
    "gpu_util_max",
    "gpu_util_avg",
    "cpu_avg_percent",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def metric_row_counts() -> tuple[int, int, int]:
    """Direct (gpu_metrics, cpu_metrics, log_entries) row counts.

    Deliberately bypasses the store and the API. A store-level count could
    in principle be fooled by the same code that wrote the rows; a direct
    `SELECT count(*)` cannot.
    """
    async with get_session() as session:
        gpu = await session.execute(select(func.count()).select_from(GPUMetricModel))
        cpu = await session.execute(select(func.count()).select_from(CPUMetricModel))
        logs = await session.execute(select(func.count()).select_from(LogEntryModel))
        return gpu.scalar_one(), cpu.scalar_one(), logs.scalar_one()


def assert_all_zero(summary) -> None:
    """Every one of the seven summary fields is an integer 0."""
    for field in SUMMARY_FIELDS:
        value = getattr(summary, field)
        assert value == 0, f"{field} was {value!r}, expected 0"
        assert isinstance(value, int), f"{field} was {value!r}, expected an int"


def assert_zero_fields(summary, expected_zero) -> None:
    """The named fields are integer 0 - used for one-sided series."""
    for field in expected_zero:
        value = getattr(summary, field)
        assert value == 0, f"{field} was {value!r}, expected 0"
        assert isinstance(value, int), f"{field} was {value!r}, expected an int"


def assert_series_is_empty(metrics) -> None:
    """
    Emptiness is the arrays, never the summary.

    An all-zero summary is exactly what a genuinely idle GPU produces, so
    a client (or a test) that branches on `summary` cannot tell "nothing
    collected" from "measured nothing" (issue #35, AC-4/AC-12).
    """
    assert metrics.gpu_metrics == []
    assert metrics.cpu_metrics == []
    assert_all_zero(metrics.summary)


async def seed_rows(store: DatabaseStore, job_id: str) -> None:
    """Write metrics and logs for a job without going through a read."""
    generated = await store._generate_job_metrics(job_id)
    assert generated is not None
    await store._store_job_metrics(job_id, generated)
    await store._store_job_logs(job_id, store._generate_job_logs(job_id))


# ---------------------------------------------------------------------------
# U1 / AC-12: _calculate_summary is total
# ---------------------------------------------------------------------------


async def test_summary_is_total_for_zero_samples(store_off):
    """
    AC-12: _calculate_summary([], []) is total - seven zeros, no exception.

    This is what lets an empty series be a 200 rather than a 500, so it is
    pinned independently of the store: a future "simplification" of the
    `_avg` helper that drops `if values else 0` turns every empty metrics
    response into a ZeroDivisionError (R6).
    """
    summary = store_off._calculate_summary([], [])

    assert_all_zero(summary)


async def test_summary_is_total_for_one_sided_series(store_off):
    """
    AC-12: a GPU-only and a CPU-only series are both total as well.

    The GPU branch is guarded by ternaries on the *list*, so a CPU-only
    series takes the same placeholder path as the empty one.
    """
    job = await store_off.create_job(create_job_spec(name="one-sided"))
    generated = await store_off._generate_job_metrics(job.job_id)
    assert generated is not None

    # CPU-only: all six GPU fields fall back to the placeholder branch,
    # and only the CPU field carries real data.
    cpu_only = store_off._calculate_summary([], generated.cpu_metrics)
    assert cpu_only.cpu_avg_percent > 0
    assert_zero_fields(cpu_only, [f for f in SUMMARY_FIELDS if f != "cpu_avg_percent"])

    # GPU-only: the mirror image. Only the CPU field is zero, and it is
    # zero because _avg short-circuits on the empty list.
    gpu_only = store_off._calculate_summary(generated.gpu_metrics, [])
    assert gpu_only.gpu_util_max > 0
    assert gpu_only.gpu_memory_avg_mb > 0
    assert_zero_fields(gpu_only, ["cpu_avg_percent"])


# ---------------------------------------------------------------------------
# U2 / AC-4: an existing job with nothing stored
# ---------------------------------------------------------------------------


async def test_get_job_metrics_returns_empty_series_when_flag_off(store_off):
    """AC-4: existing job, no rows, flag off -> empty series, 200-shaped."""
    job = await store_off.create_job(create_job_spec(name="no-samples"))
    assert await metric_row_counts() == (0, 0, 0)

    metrics = await store_off.get_job_metrics(job.job_id)

    assert metrics is not None, "an existing job must not 404"
    assert metrics.job_id == job.job_id
    assert_series_is_empty(metrics)
    # And nothing was written to make that answer.
    assert await metric_row_counts() == (0, 0, 0)


# ---------------------------------------------------------------------------
# U3 / AC-5: an existing job with no logs
# ---------------------------------------------------------------------------


async def test_get_job_logs_returns_empty_list_when_flag_off(store_off):
    """AC-5: existing job, no logs, flag off -> [], not 404."""
    job = await store_off.create_job(create_job_spec(name="no-logs"))

    logs = await store_off.get_job_logs(job.job_id)

    assert logs == []
    assert await metric_row_counts() == (0, 0, 0)


# ---------------------------------------------------------------------------
# U4 / U5 / AC-1 / AC-2 / AC-3: unknown ids
# ---------------------------------------------------------------------------


async def test_get_job_metrics_returns_none_for_unknown_job_either_flag(
    request, monkeypatch
):
    """AC-1/AC-3: an unknown job is None with the flag on AND off.

    This one already worked before #35 (the store checked the job first).
    It is pinned here so a "fix" that moved the existence check inside the
    SEED_DEMO_DATA gate - turning a live 404 into a 200 - fails.
    """
    for flag in (False, True):
        monkeypatch.setattr(settings, "SEED_DEMO_DATA", flag)
        store = DatabaseStore()
        store._seeded = True
        assert await store.get_job_metrics("job-does-not-exist") is None, flag
        await store.stop_all_log_streams()


async def test_get_job_logs_returns_none_for_unknown_job_either_flag(
    monkeypatch,
):
    """AC-2/AC-3: the logs path now matches the metrics path.

    `get_job_logs` is declared `Optional[List[str]]` precisely so the
    route's LOGS_NOT_FOUND branch could be reachable; until #35 it always
    returned 64 invented lines for any id at all, and nothing was stored -
    so the branch was dead code in both flag states.
    """
    for flag in (False, True):
        monkeypatch.setattr(settings, "SEED_DEMO_DATA", flag)
        store = DatabaseStore()
        store._seeded = True
        assert await store.get_job_logs("job-does-not-exist") is None, flag
        await store.stop_all_log_streams()
    assert await metric_row_counts() == (0, 0, 0)


# ---------------------------------------------------------------------------
# U6 / AC-6: nodes
# ---------------------------------------------------------------------------


async def test_get_node_metrics_empty_series_for_existing_node(store_off):
    """AC-6: a node that registered but has reported nothing is not broken."""
    node = await store_off.create_node(create_node_spec(node_id="quiet-node"))

    metrics = await store_off.get_node_metrics("quiet-node")

    assert metrics is not None
    assert metrics.job_id == "node:quiet-node"
    assert_series_is_empty(metrics)
    assert await metric_row_counts() == (0, 0, 0)
    assert node.node_id == "quiet-node"


async def test_get_node_metrics_none_for_unknown_node_either_flag(monkeypatch):
    """AC-6: an unknown node is None in both flag states."""
    for flag in (False, True):
        monkeypatch.setattr(settings, "SEED_DEMO_DATA", flag)
        store = DatabaseStore()
        store._seeded = True
        assert await store.get_node_metrics("no-such-node") is None, flag
        await store.stop_all_log_streams()


# ---------------------------------------------------------------------------
# U7 / AC-7: reads have no side effects  (the class-of-bug guard)
# ---------------------------------------------------------------------------


async def test_reads_do_not_write_with_flag_off(store_off, clean_database):
    """
    AC-7: with demo data off, no read path writes a single row.

    All seven read paths, twice each, against a job and a node that exist
    and have nothing stored. The assertion is on the three raw tables.

    This is the assertion that catches the whole class of bug rather than
    one call site: re-introduce generation anywhere and the counts move.
    """
    job = await store_off.create_job(create_job_spec(name="read-only-probe"))
    await store_off.create_node(create_node_spec(node_id="read-only-node"))

    before = await metric_row_counts()
    assert before == (0, 0, 0)

    for _ in range(2):
        assert await store_off.get_job_metrics(job.job_id) is not None
        assert await store_off.get_node_metrics("read-only-node") is not None
        assert await store_off.get_job_logs(job.job_id) == []
        assert await store_off.get_job_metrics("job-unknown") is None
        assert await store_off.get_node_metrics("node-unknown") is None
        assert await store_off.get_job_logs("job-unknown") is None

    assert await metric_row_counts() == before


async def test_reads_do_not_write_when_the_resource_already_has_rows(
    store_off, clean_database
):
    """
    AC-7, second half: reads over EXISTING rows are side-effect free too.

    This is the case that matters in production, where a real agent has
    already written samples. The read path must return what is stored and
    add nothing - in particular it must not append a generated series to a
    real one.
    """
    job = await store_off.create_job(create_job_spec(name="has-rows"))
    await seed_rows(store_off, job.job_id)

    stored_gpu, stored_cpu, stored_logs = await metric_row_counts()
    assert stored_gpu > 0 and stored_cpu > 0 and stored_logs > 0

    for _ in range(2):
        metrics = await store_off.get_job_metrics(job.job_id)
        assert metrics is not None
        assert len(metrics.gpu_metrics) == stored_gpu
        assert len(metrics.cpu_metrics) == stored_cpu
        logs = await store_off.get_job_logs(job.job_id)
        assert logs is not None and len(logs) == stored_logs

    assert await metric_row_counts() == (stored_gpu, stored_cpu, stored_logs)


async def test_demo_mode_reads_are_idempotent(store_on, clean_database):
    """
    AC-10: demo mode still generates and persists, but only once.

    AC-7 as written says counts must be unchanged "the flag either on or
    off", which contradicts AC-10 ("persist-on-read still happens"). The
    two can only be reconciled this way: with the flag on, the FIRST read
    of an unpopulated resource writes the demo series (documented,
    load-bearing behaviour - several existing tests rely on it) and every
    read after that adds nothing.
    """
    job = await store_on.create_job(create_job_spec(name="demo-mode"))

    metrics = await store_on.get_job_metrics(job.job_id)
    assert metrics is not None
    assert len(metrics.gpu_metrics) == 121
    assert len(metrics.cpu_metrics) == 121
    first = await metric_row_counts()
    assert first == (121, 121, 0)

    logs = await store_on.get_job_logs(job.job_id)
    assert logs is not None and len(logs) == 64
    second = await metric_row_counts()
    assert second == (121, 121, 64)

    # Determinism across two reads, which is what the caching used to buy.
    for _ in range(2):
        again = await store_on.get_job_metrics(job.job_id)
        assert again == metrics
        assert await store_on.get_job_logs(job.job_id) == logs

    assert await metric_row_counts() == second


# ---------------------------------------------------------------------------
# U8 / U9 / AC-8 / AC-9: the log stream (the fifth fabrication site)
# ---------------------------------------------------------------------------


async def test_log_stream_does_not_fabricate_when_flag_off(store_off, flag_off):
    """
    AC-8 first half: the stream is refused outright, and writes nothing.

    _stream_worker invented a line AND inserted it into log_entries every
    1.4s for any RUNNING job. No read was required - subscribing was
    enough. This asserts the refusal, that no line is delivered, and that
    log_entries is still empty after three ticks (4.7s).
    """
    job = await store_off.create_job(create_job_spec(name="ws-off"))
    await store_off.update_job(job.job_id, status=JobStatus.RUNNING)

    received: list = []
    try:
        task = await store_off.start_log_stream(job.job_id, received.append)

        assert task is None, "no stream task may be created with the flag off"
        assert store_off.log_stream_available() is False

        await asyncio.sleep(THREE_TICKS)

        assert received == []
        assert await metric_row_counts() == (0, 0, 0)
    finally:
        # Always stop the stream, even when the assertion above failed. An
        # orphaned worker keeps writing rows every 1.4s, which turns one
        # clear failure into a cascade of "database is locked" in every
        # test that follows - including a mutation probe that should be
        # reporting on exactly this guard.
        await store_off.stop_all_log_streams()


async def test_log_stream_stops_when_the_flag_is_flipped_off(store_on, flag_on):
    """
    AC-9 / R1: the partial-fix probe.

    Gating only the four HTTP call sites and leaving _stream_worker alone
    is the failure mode most likely to ship green: the worker keeps writing
    an invented row every 1.4s with nobody reading anything. Starting the
    stream with the flag on and then turning it off exercises the worker's
    own guard, which start_log_stream's early return cannot cover.
    """
    job = await store_on.create_job(create_job_spec(name="ws-flipped"))
    await store_on.update_job(job.job_id, status=JobStatus.RUNNING)

    received: list = []
    try:
        task = await store_on.start_log_stream(job.job_id, received.append)
        assert task is not None

        # Wait for the first line, then measure the row count it left
        # behind, so "stopped" is measured relative to real activity.
        for _ in range(60):
            if received:
                break
            await asyncio.sleep(0.1)
        assert received, "the stream should be running while the flag is on"
        after_first = await metric_row_counts()
        assert after_first[2] > 0

        settings.SEED_DEMO_DATA = False

        await asyncio.sleep(THREE_TICKS)
        stopped_at = await metric_row_counts()
        # One in-flight tick is tolerable; more than one is not.
        assert stopped_at[2] <= after_first[2] + 1, (
            "the worker kept writing log_entries after the flag was "
            f"turned off: {after_first[2]} -> {stopped_at[2]}"
        )

        # And it really has stopped, not merely slowed down.
        await asyncio.sleep(THREE_TICKS)
        assert (await metric_row_counts())[2] == stopped_at[2]
    finally:
        settings.SEED_DEMO_DATA = True
        await store_on.stop_all_log_streams()


async def test_log_stream_unchanged_when_flag_on(store_on, flag_on):
    """AC-8 second half / AC-10 negative control: demo mode still streams."""
    job = await store_on.create_job(create_job_spec(name="ws-on"))
    await store_on.update_job(job.job_id, status=JobStatus.RUNNING)

    received: list = []
    try:
        task = await store_on.start_log_stream(job.job_id, received.append)
        assert task is not None
        assert store_on.log_stream_available() is True

        for _ in range(60):
            if received:
                break
            await asyncio.sleep(0.1)
    finally:
        await store_on.stop_all_log_streams()

    assert len(received) > 0
    # Every delivered line was also persisted, as it always was.
    assert (await metric_row_counts())[2] >= len(received)


async def test_service_reports_log_stream_availability(monkeypatch):
    """
    The WebSocket route asks the service, not the store, whether it can
    stream at all. This is the seam between the two, so it is pinned for
    both flag states - if it were hardcoded, the route would either close
    a working socket or hold open a dead one.
    """
    service = JobService()

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    assert service.log_stream_available() is False

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)
    assert service.log_stream_available() is True


async def test_service_start_log_stream_returns_none_when_flag_off(
    seeding_disabled, monkeypatch
):
    """
    The service propagates the store's refusal instead of inventing a
    task, which is what lets the route send a close frame rather than
    hanging on a socket that will never tick.
    """
    service = JobService()
    job = await service.create_job(create_job_spec(name="service-ws-off"))

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    assert service.log_stream_available() is False
    assert await service.start_log_stream(job.job_id, lambda line: None) is None
    assert await metric_row_counts() == (0, 0, 0)

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)
    assert service.log_stream_available() is True
    task = await service.start_log_stream(job.job_id, lambda line: None)
    try:
        assert task is not None
    finally:
        await service.stop_log_stream(job.job_id)


# ---------------------------------------------------------------------------
# U10 / AC-11: the generators survive as the determinism oracle
# ---------------------------------------------------------------------------


async def test_generators_still_deterministic_with_flag_off(store_off, flag_off):
    """
    AC-11: the private generators are neither deleted nor flag-dependent.

    They stay in the store as the oracle pinning the shape of the demo
    dataset. test_store.py::TestSeedData::test_seed_data_deterministic is
    the original guard and is deliberately left untouched; this is the
    same guarantee stated with the flag off, which is where they will
    actually be called from once demo data is a local-only mode.
    """
    store = preloaded_store()
    await store.create_job_with_id(job_state("job-1050"))

    metrics1 = await store._generate_job_metrics("job-1050")
    metrics2 = await store._generate_job_metrics("job-1050")
    assert metrics1 is not None and metrics2 is not None
    assert [m.memory_used_mb for m in metrics1.gpu_metrics] == [
        m.memory_used_mb for m in metrics2.gpu_metrics
    ]
    assert [m.utilization_percent for m in metrics1.gpu_metrics] == [
        m.utilization_percent for m in metrics2.gpu_metrics
    ]
    assert [m.cpu_percent for m in metrics1.cpu_metrics] == [
        m.cpu_percent for m in metrics2.cpu_metrics
    ]

    def bodies(lines):
        # Lines are stamped with the current time; compare the messages.
        return [line[19:] for line in lines]

    assert bodies(store._generate_job_logs("job-1050")) == bodies(
        store._generate_job_logs("job-1050")
    )

    node = await store._generate_node_metrics("node-alpha")
    assert node is None, "no such node exists in a clean database"
    assert settings.SEED_DEMO_DATA is False


# ---------------------------------------------------------------------------
# AC-4 / R4: the empty series is representable as it stands
# ---------------------------------------------------------------------------


async def test_empty_series_round_trips_through_the_response_model(store_off):
    """
    The empty series is representable as-is: no schema change, no optional
    fields, no NaN. Guards the temptation to make `summary` optional
    "because sometimes there is no summary" (R4).
    """
    from shared.schemas.job_metrics import JobMetrics

    job = await store_off.create_job(create_job_spec(name="round-trip"))
    metrics = await store_off.get_job_metrics(job.job_id)

    revalidated = JobMetrics.model_validate(
        JobMetrics.model_validate(metrics.model_dump()).model_dump()
    )
    assert revalidated == metrics
    assert_series_is_empty(revalidated)
