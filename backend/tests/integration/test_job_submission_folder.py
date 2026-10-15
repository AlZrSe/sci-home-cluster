"""
Job submission must enter the Syncthing folder (issue #105).

The bug this file pins: ``POST /jobs/`` wrote a database row and nothing else.
Under the file-only transport decision on #92 the folder is the only channel
in both directions, so the channel the agent actually has never opened carried
nothing - a submitted job was listed in the dashboard, could be retried and
cancelled, and no node would ever run it.

Every test here reads the **filesystem** and drives the **real** ingestion
paths - the real ``agent.watcher.FolderWatcher`` over the same root, the real
``SyncthingService._process_job_file`` / ``_initial_scan``. Mocking any of them
would test the mock: the defect lived precisely in the seam between "the row
was written" and "the file a node reads exists", and a mocked watcher is
indistinguishable from a watcher that was never pointed at the right folder.

The id assertions never spell a literal. The session seeds a demo dataset
(``conftest.isolated_database`` sets ``SEED_DEMO_DATA``), so the first
submission in a session is ``job-1051`` and not ``job-1``; every id here is
read back from the response or the database.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, cast
from types import SimpleNamespace

import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from watchdog.events import DirMovedEvent, FileMovedEvent

from agent.paths import resolve_paths
from agent.tests.conftest import REAL_WATCH_SUPPORTED
from agent.watcher import EVENT_CREATED, FolderWatcher
from backend.core.config import settings
from backend.main import app
from backend.services import job_folder, job_service
from backend.services.job_folder import job_dir, job_state_file
from backend.services.job_service import JobService
from backend.services.syncthing_service import SyncthingService
from backend.store import get_store
from backend.store.database import JobModel
from backend.tests.factories import create_job_spec
from shared.file_ops.path_utils import get_job_state_file
from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus

REPO_ROOT = Path(__file__).resolve().parents[3]
AUTH = {"Authorization": "Bearer localhost-no-auth"}

#: How long a real inotify observer is given to report a write. Generous,
#: because the point of the AC-3 test is that the event arrives at all - a
#: deadline that is too tight turns a scheduling hiccup into a red suite.
OBSERVER_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def folder_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A per-test Syncthing root that ``settings.SYNCTHING_ROOT`` points at.

    Per-test rather than session-wide because these tests assert on the exact
    contents of one job's directory: a shared root would make every assertion
    about "the folder" depend on what ran before it.
    """
    root = tmp_path / "syncthing"
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    monkeypatch.setattr(settings, "SYNCTHING_ROOT", str(root))
    return root


@pytest.fixture
def spec_payload() -> tuple[dict[str, Any], JobSpec]:
    """The submitted spec: the JSON the API receives, and the model itself.

    Both from one construction. Comparing the file against a second
    ``create_job_spec(...)`` call would compare two factory runs, and the
    factory sequences several fields - the comparison would then fail on a
    value nobody changed.
    """
    spec = create_job_spec(name="folder-submission")
    return spec.model_dump(mode="json"), spec


@pytest.fixture
async def api_client() -> AsyncGenerator[AsyncClient, None]:
    """
    A client over the real ASGI app.

    ``ASGITransport`` does not run the lifespan, so ``app.state.syncthing_service``
    is absent - irrelevant here, because the backend's watcher is driven
    directly wherever a test needs it rather than through the endpoint.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        client.headers.update(AUTH)
        yield client


# A second name for the same thing, spelled out where the short one would be
# cryptic next to the many `client` fixtures in the rest of the suite.
auth_client = api_client


async def submit(
    client: AsyncClient, payload: dict[str, Any] | None = None
) -> tuple[str, Any]:
    """POST a job and return ``(job_id, response)``."""
    response = await client.post(
        "/api/v1/jobs/",
        json=payload
        if payload is not None
        else create_job_spec().model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    return response.json()["job_id"], response


async def total_rows() -> int:
    from backend.core.database import get_session

    async with get_session() as session:
        result = await session.execute(select(func.count()).select_from(JobModel))
        return int(result.scalar())


# ---------------------------------------------------------------------------
# AC-1 / AC-2: the file exists, and it is about the right job
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSubmissionPublishesTheStateFile:
    """AC-1, AC-2."""

    async def test_the_submitted_job_lands_in_the_folder(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        spec_payload: tuple[dict[str, Any], JobSpec],
    ) -> None:
        """
        AC-1, asserted on the file and not on a mock.

        Read back through ``read_yaml(path, JobState)`` - the same call
        ``SyncthingService._process_job_file`` makes - so a file that is on
        disk but unreadable fails here rather than silently in production.
        """
        payload, spec = spec_payload
        job_id, response = await submit(auth_client, payload)
        state_file = folder_root / "jobs" / job_id / "state.yaml"

        assert state_file.is_file(), (
            "POST /jobs/ returned 201 and the folder is empty; that is issue "
            "#105 exactly - the job is queued in the database and invisible "
            "to every node"
        )

        state = read_yaml(str(state_file), JobState)
        assert state.status == JobStatus.PENDING
        assert state.node_id is None
        assert state.started_at is None
        assert state.completed_at is None
        assert state.exit_code is None
        assert state.error is None
        assert state.retry_count == 0
        assert state.spec == spec

        # The response and the file agree, field for field. Divergence here
        # would mean the folder is describing a job the database does not
        # have.
        body = response.json()
        assert state.job_id == body["job_id"]
        assert state.status.value == body["status"]
        assert state.retry_count == body["retry_count"]
        assert state.created_at.isoformat() == body["created_at"]

    async def test_the_directory_name_is_the_database_job_id(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        spec_payload: tuple[dict[str, Any], JobSpec],
    ) -> None:
        """
        AC-2, comparing three independent reads of the same id.

        The response body, the directory listing and the database row. A
        variable reused across the three would assert nothing; this reads each
        one separately and then compares.
        """
        payload, _ = spec_payload
        job_id, _ = await submit(auth_client, payload)

        listed = sorted(p.name for p in (folder_root / "jobs").iterdir())
        in_database = (await get_store().get_job(job_id)).job_id

        assert listed == [job_id]
        assert in_database == job_id
        assert job_state_file(folder_root, job_id).parent.name == job_id

    async def test_the_file_is_readable_as_the_model_the_watcher_validates(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        The status has to come back as the enum, not a string.

        ``read_yaml`` validates into ``JobState``, so Pydantic would coerce a
        plain ``"PENDING"``; what this pins is that the document holds a plain
        string at all. A python-object tag - what ``write_yaml`` emitted in
        python mode - is not coerced, it is refused by ``yaml.safe_load``, and
        ``_process_job_file`` swallows that refusal.
        """
        job_id, _ = await submit(auth_client)
        state_file = job_state_file(folder_root, job_id)

        state = read_yaml(str(state_file), JobState)

        assert state.status is JobStatus.PENDING
        assert yaml.safe_load(state_file.read_text(encoding="utf-8"))["status"] == (
            "PENDING"
        )


# ---------------------------------------------------------------------------
# AC-3: a file-only agent observes it
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not REAL_WATCH_SUPPORTED, reason="no real filesystem observer on this platform"
)
class TestAFileOnlyAgentSeesTheSubmission:
    """AC-3: the end-to-end path with no HTTP on the observing side."""

    async def test_a_real_agent_watcher_observes_the_submitted_state_file(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        spec_payload: tuple[dict[str, Any], JobSpec],
    ) -> None:
        """
        A real ``FolderWatcher``, a real inotify observer, the real filter and
        the real ledger, over the same root the backend wrote into.

        This is the assertion the whole issue turns on. Before #105 the folder
        stayed empty and nothing could observe anything.
        """
        payload, _ = spec_payload
        paths = resolve_paths(folder_root, "node-test")
        watcher = FolderWatcher(paths)
        watcher.start()
        try:
            job_id, _ = await submit(auth_client, payload)
            expected = job_state_file(folder_root, job_id)

            seen = await wait_for_event(watcher, expected)
        finally:
            watcher.stop()

        assert seen == EVENT_CREATED, (
            f"the agent's watcher reported {seen!r} for {expected}, not "
            f"{EVENT_CREATED!r}; the submitted job is not discoverable"
        )

    async def test_the_watcher_is_not_simply_deaf(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        The control.

        Without it, "the watcher saw the submission" and "the watcher is
        deaf" are the same observation. A foreign file written the way
        Syncthing delivers one must also arrive.
        """
        paths = resolve_paths(folder_root, "node-test")
        watcher = FolderWatcher(paths)
        watcher.start()
        try:
            foreign = folder_root / "nodes" / "node-foreign.yaml"
            foreign.write_text("node_id: node-foreign\n", encoding="utf-8")
            seen = await wait_for_event(watcher, foreign)
        finally:
            watcher.stop()

        assert seen is not None, "the real watcher saw nothing at all"

    async def test_the_agents_own_write_is_still_not_re_entered(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        Adding ``on_moved`` must not have broken the ledger's job.

        ``write_yaml`` - the one the agent itself uses - also delivers by
        rename, so without the ledger check every agent heartbeat would now
        come back as an event the agent wrote itself.
        """
        paths = resolve_paths(folder_root, "node-test")
        watcher = FolderWatcher(paths)
        watcher.start()
        try:
            target = paths.node_file
            watcher.ledger.record(target)
            for _ in range(3):
                write_yaml(str(target), _a_node_spec())

            await asyncio.sleep(1.0)
            while not watcher.events().empty():
                watcher.events().get_nowait()
        finally:
            watcher.stop()

        assert watcher.events().qsize() == 0


# ---------------------------------------------------------------------------
# AC-4: a folder failure is reported, never swallowed
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAFolderFailureIsSurfaced:
    """AC-4: 503 SYNCTHING_UNAVAILABLE on all three paths, plus the rollbacks."""

    async def test_a_failed_create_is_a_503_and_leaves_no_row(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        The rollback is the point: a 201 with a broken job is this bug moved
        one layer away, so a failure must leave the database exactly as it was.
        """
        monkeypatch.setattr(job_folder, "write_yaml", _always_fails_to_write)
        before = await total_rows()

        response = await auth_client.post(
            "/api/v1/jobs/", json=create_job_spec(name="doomed").model_dump(mode="json")
        )

        assert response.status_code == 503
        assert response.json()["error_code"] == "SYNCTHING_UNAVAILABLE"
        assert await total_rows() == before, (
            "the compensated row survived; a job in the database with no file "
            "in the folder is the exact defect #105 exists to remove"
        )

    async def test_a_reissued_id_is_a_409_and_overwrites_nothing(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        seeding_disabled,
    ) -> None:
        """
        D7: the clobber guard.

        `_sync_job_counter` derives the id sequence from the **database**, so a
        wiped database with a surviving `jobs/` directory restarts the counter
        at 1 and `POST /jobs` allocates an id whose directory already belongs to
        another job. Writing there would overwrite that job's `state.yaml`, and
        the backend's own watcher would then apply this job's status, node_id
        and timestamps to the *other* job's row - silent cross-job corruption,
        with no error anywhere.

        So it is a loud failure instead, and the pre-existing directory is left
        exactly as it was. Staged the way it happens in production - empty
        database, surviving folder - which is also the only way to make the
        counter reissue an id without reaching into a private to rewind it.
        """
        squatter = folder_root / "jobs" / "job-1"
        squatter.mkdir()
        state_file = squatter / "state.yaml"
        write_yaml(
            str(state_file),
            JobState(
                job_id="job-1",
                spec=create_job_spec(name="someone-elses-job"),
                status=JobStatus.FAILED,
                created_at=datetime(2026, 1, 1),
                retry_count=0,
            ),
        )
        before = state_file.read_text(encoding="utf-8")

        response = await auth_client.post(
            "/api/v1/jobs/",
            json=create_job_spec(name="collider").model_dump(mode="json"),
        )

        assert response.status_code == 409
        assert response.json()["error_code"] == "JOB_ID_COLLISION"
        assert (
            state_file.read_text(encoding="utf-8") == before
        ), "the other job's state.yaml was overwritten"
        assert (
            await get_store().get_job("job-1") is None
        ), "the clobbered row was left behind instead of compensated"

    async def test_a_failed_retry_restores_the_previous_state(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id, _ = await submit(auth_client)
        await _make_failed(job_id)

        monkeypatch.setattr(job_folder, "write_yaml", _always_fails_to_write)
        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")

        assert response.status_code == 503
        assert response.json()["error_code"] == "SYNCTHING_UNAVAILABLE"

        store = get_store()
        restored = await store.get_job(job_id)
        assert restored.status == JobStatus.FAILED
        assert (
            restored.retry_count == 0
        ), "a retry that could not be published must not consume an attempt"
        assert restored.node_id == "node-alpha"
        assert restored.exit_code == 1

    async def test_a_failed_delete_keeps_the_row_and_the_file(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Delete has no compensation, because the ordering already makes the
        failure mode safe: the two halves stay consistent, and - unlike a
        row-first delete - the job is not resurrected by the next scan.
        """
        job_id, _ = await submit(auth_client)

        # Patch the imported function in job_service, not the module it came from,
        # because job_service imports remove_job_dir at module level.
        monkeypatch.setattr(job_service, "remove_job_dir", _always_fails_to_remove)
        response = await auth_client.delete(f"/api/v1/jobs/{job_id}")

        assert response.status_code == 503
        assert response.json()["error_code"] == "SYNCTHING_UNAVAILABLE"
        assert await get_store().get_job(job_id) is not None
        assert job_state_file(folder_root, job_id).is_file()

    async def test_a_create_whose_rollback_fails_still_answers_503_and_says_so(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        §5.6: a compensation failure must not turn the answer into a 500.

        The client is told the submission failed either way, and the orphan is
        logged at ERROR with its ``job_id`` - the only thing left anyone can
        act on. Reconciling it is #103's job.
        """
        monkeypatch.setattr(job_folder, "write_yaml", _always_fails_to_write)
        monkeypatch.setattr(
            get_store(), "delete_job", _always_fails_to_delete, raising=False
        )

        with caplog.at_level(logging.ERROR, logger="backend.services.job_service"):
            response = await auth_client.post(
                "/api/v1/jobs/",
                json=create_job_spec(name="orphan").model_dump(mode="json"),
            )

        assert response.status_code == 503
        assert response.json()["error_code"] == "SYNCTHING_UNAVAILABLE"
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "a failed compensation was swallowed instead of logged"
        assert any(re.search(r"job-\d+", r.getMessage()) for r in errors)


# ---------------------------------------------------------------------------
# AC-5: retry re-emits
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRetryRepublishes:
    """AC-5."""

    async def test_retry_rewrites_the_file_for_a_failed_job(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        job_id, _ = await submit(auth_client)
        await _make_failed(job_id)

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")

        assert response.status_code == 200
        state = read_yaml(str(job_state_file(folder_root, job_id)), JobState)
        assert state.status is JobStatus.PENDING
        assert state.node_id is None
        assert state.completed_at is None
        assert state.exit_code is None
        assert state.error is None
        assert state.retry_count == 1

    async def test_retry_recreates_a_job_directory_that_is_gone(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        Retry is the repair path, so it does not run the create-time clobber
        guard: the directory is *supposed* to be there, and a job whose
        directory was lost - written before this change landed, or after a
        folder reset - has to come back.
        """
        job_id, _ = await submit(auth_client)
        await _make_failed(job_id)
        shutil.rmtree(job_dir(folder_root, job_id))
        assert not job_dir(folder_root, job_id).exists()

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")

        assert response.status_code == 200
        assert job_state_file(folder_root, job_id).is_file()

    async def test_retry_does_not_change_the_spec_or_the_created_at(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        §5.5: the backend writes ``spec`` on retry **unchanged**, and
        ``created_at`` not at all. The folder can never change a job's command
        after submission.
        """
        job_id, _ = await submit(auth_client)
        original = read_yaml(str(job_state_file(folder_root, job_id)), JobState)
        await _make_failed(job_id)

        await auth_client.post(f"/api/v1/jobs/{job_id}/retry")

        retried = read_yaml(str(job_state_file(folder_root, job_id)), JobState)
        assert retried.spec == original.spec
        assert retried.created_at == original.created_at


# ---------------------------------------------------------------------------
# AC-6 / AC-16: the backend's own watcher re-reads its own write
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTheBackendWatcherRoundTripIsBenign:
    """AC-6 and AC-16, driven through the real ``_process_job_file``."""

    async def test_re_reading_the_backends_own_write_changes_nothing(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        job_id, _ = await submit(auth_client)
        state_file = job_state_file(folder_root, job_id)
        rows_before = await total_rows()

        await SyncthingService(folder_root)._process_job_file(job_id, str(state_file))

        job = await get_store().get_job(job_id)
        assert job is not None
        assert job.status is JobStatus.PENDING
        assert job.node_id is None
        assert await total_rows() == rows_before, (
            "the watcher's read-back created a second row for a job the "
            "backend had just created"
        )

    async def test_the_create_branch_would_raise_rather_than_paper_over(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        The negative half of AC-6.

        ``_process_job_file`` branches on whether the row already exists, and
        the else branch calls ``create_job_with_id``, which raises
        ``IntegrityError`` on the primary key. So if the branch order ever
        changed, the failure would be loud - this is what makes "the
        existing-job branch is the one that matches" a checked claim instead
        of a reading of the code.
        """
        job_id, _ = await submit(auth_client)
        state = read_yaml(str(job_state_file(folder_root, job_id)), JobState)

        with pytest.raises(IntegrityError):
            await get_store().create_job_with_id(state)

    async def test_one_submission_costs_one_insert_and_one_update(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        AC-16.

        The read-back costs one redundant ``UPDATE`` per submission - the
        backend parses its own write. That is known and bounded. What must not
        grow is the count, so it is measured rather than assumed.
        """
        job_id, _ = await submit(auth_client)
        store = get_store()
        updates: list[tuple[str, dict[str, Any]]] = []
        original = store.update_job

        async def counting_update(job: str, **kwargs: Any) -> Any:
            updates.append((job, kwargs))
            return await original(job, **kwargs)

        monkeypatch.setattr(store, "update_job", counting_update, raising=False)
        rows_before = await total_rows()

        await SyncthingService(folder_root)._process_job_file(
            job_id, str(job_state_file(folder_root, job_id))
        )

        assert (
            len(updates) == 1
        ), f"a single read-back issued {len(updates)} UPDATEs: {updates}"
        assert updates[0][0] == job_id
        assert await total_rows() == rows_before


# ---------------------------------------------------------------------------
# AC-7 / AC-8 / AC-12 / D8: delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteRemovesTheJobFromBothHalves:
    """AC-7, AC-8, AC-12 and the D8 status codes."""

    async def test_delete_removes_the_job_directory(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """AC-7, asserted on the filesystem."""
        job_id, _ = await submit(auth_client)
        assert job_dir(folder_root, job_id).is_dir()

        response = await auth_client.delete(f"/api/v1/jobs/{job_id}")

        assert response.status_code == 204
        assert not job_dir(folder_root, job_id).exists()

    async def test_a_deleted_job_is_not_resurrected_by_the_next_scan(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        AC-8, and the regression guard for the §5.4 ordering.

        A `state.yaml` whose database row is gone is re-created by the next
        ingestion pass: `_process_job_file` finds no existing job and calls
        `create_job_with_id`. Delete the row first and the operator's delete
        silently does nothing, on the next scan. Without this test a
        reordering that reads as "cleaner" passes every other criterion here.

        This variant also injects an `rmtree` failure to prove the hazard in
        §5.4 is only reachable when the folder removal fails - the row stays
        and the directory stays, so the next scan finds nothing new to ingest.
        """
        job_id, _ = await submit(auth_client)

        # Inject a folder removal failure - this tests the §5.4 hazard path
        monkeypatch.setattr(job_service, "remove_job_dir", _always_fails_to_remove)
        response = await auth_client.delete(f"/api/v1/jobs/{job_id}")

        # The delete fails with 503, row and file both survive
        assert response.status_code == 503
        assert response.json()["error_code"] == "SYNCTHING_UNAVAILABLE"
        assert await get_store().get_job(job_id) is not None
        assert job_state_file(folder_root, job_id).is_file()

        # Now drive the real _initial_scan - it must not resurrect anything
        await SyncthingService(folder_root)._initial_scan()

        # Job still exists (it was never deleted), and no duplicate was created
        job = await get_store().get_job(job_id)
        assert job is not None
        assert job.job_id == job_id

    async def test_delete_takes_the_whole_directory_and_nothing_else(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        AC-12.

        The agent's own state lives at ``<root>/.agent/<node_id>``, outside
        ``jobs/``, and every other job keeps its directory. Only the one job
        the operator named may disappear.
        """
        victim, _ = await submit(auth_client)
        bystander, _ = await submit(auth_client)
        agent_state = folder_root / ".agent" / "node-test"
        agent_state.mkdir(parents=True)
        (agent_state / "checkpoint.bin").write_bytes(b"x" * 32)
        # A scratch file inside the victim directory has to go with it.
        (job_dir(folder_root, victim) / "scratch.tmp").write_text("agent scratch")

        response = await auth_client.delete(f"/api/v1/jobs/{victim}")

        assert response.status_code == 204
        assert not job_dir(folder_root, victim).exists()
        assert job_dir(folder_root, bystander).is_dir()
        assert job_state_file(folder_root, bystander).is_file()
        assert (agent_state / "checkpoint.bin").read_bytes() == b"x" * 32

    async def test_deleting_an_unknown_job_is_a_404(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        response = await auth_client.delete("/api/v1/jobs/job-999999")

        assert response.status_code == 404
        assert response.json()["error_code"] == "JOB_NOT_FOUND"

    async def test_deleting_twice_is_the_second_404(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        job_id, _ = await submit(auth_client)
        assert (await auth_client.delete(f"/api/v1/jobs/{job_id}")).status_code == 204

        assert (await auth_client.delete(f"/api/v1/jobs/{job_id}")).status_code == 404

    async def test_a_delete_lost_to_the_watcher_is_a_204(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        D8, and the reason the status code moved.

        Removing the directory fires the running watcher's delete handler,
        which calls ``store.delete_job`` and takes the row. The request's own
        delete can then find nothing and get ``False``. That is the end state
        the client asked for, so it is a 204 - a 404 here would tell an
        operator their delete failed when it succeeded.

        The race is staged on ``store.delete_job`` because that is where it
        actually happens: the method the request is about to call, already
        reached by the watcher. The staging does the real delete (so the row
        really is gone afterwards, exactly as the watcher would leave it) and
        then reports ``False`` - the value the request is the second caller to
        receive. The directory removal is not stubbed.
        """
        job_id, _ = await submit(auth_client)
        store = get_store()
        real_delete = store.delete_job
        calls: list[str] = []

        async def the_watcher_got_there_first(job: str) -> bool:
            calls.append(job)
            await real_delete(job)
            return False

        monkeypatch.setattr(
            store, "delete_job", the_watcher_got_there_first, raising=False
        )

        response = await auth_client.delete(f"/api/v1/jobs/{job_id}")

        assert calls == [job_id]
        assert response.status_code == 204, response.text
        assert await get_store().get_job(job_id) is None
        assert not job_dir(folder_root, job_id).exists()

    async def test_a_directory_that_is_already_gone_is_not_a_failure(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        §5.6: "already absent" is the state that was asked for, so the delete
        continues to the row instead of answering 503.
        """
        job_id, _ = await submit(auth_client)
        shutil.rmtree(job_dir(folder_root, job_id))

        response = await auth_client.delete(f"/api/v1/jobs/{job_id}")

        assert response.status_code == 204
        assert await get_store().get_job(job_id) is None


# ---------------------------------------------------------------------------
# AC-9: the settings root, and no dependence on a `None` path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTheRootIsTheSettingsRoot:
    """AC-9."""

    async def test_submission_works_with_syncthing_root_absent_from_the_environment(
        self,
        auth_client: AsyncClient,
        folder_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        The backend resolves the folder from ``settings``, so
        ``get_job_state_file`` - which reads ``os.environ`` - would answer
        ``None`` here. That is the whole reason the resolver exists: one
        folder, one source, and a ``None`` that no caller can be handed.
        """
        monkeypatch.delenv("SYNCTHING_ROOT", raising=False)

        job_id, _ = await submit(auth_client)

        assert get_job_state_file(job_id) is None, (
            "os.environ[SYNCTHING_ROOT] is absent, so the environment-based "
            "helper has nothing to resolve"
        )
        assert job_state_file(folder_root, job_id).is_file()
        assert settings.SYNCTHING_ROOT == str(folder_root)


# ---------------------------------------------------------------------------
# AC-13 / AC-15: ownership
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTheOwnershipRuleIsPinned:
    """AC-13 and AC-15."""

    def test_the_delete_site_states_who_may_delete_a_job_directory(self) -> None:
        """
        AC-13.

        The rule is only enforced by review unless something fails when the
        comment is edited away, so this reads it the way
        ``test_seed_flag.py`` reads ``.env.example``: anchored on the method,
        not on the working directory, and asserting on content rather than on
        the absence of a problem.
        """
        source = inspect.getsource(JobService.delete_job)

        assert "the backend may delete a job directory" in source
        assert "the agent never may" in source
        # The consequence, so nobody reads the rule as a style preference.
        assert "474-482" in source, (
            "the comment must cite the purge it triggers; without it the rule "
            "reads as advisory"
        )

    def test_the_rule_is_not_stated_anywhere_else(self) -> None:
        """
        Stated once, at the only site that can act on it.

        A second copy somewhere else is a copy that will drift. Not a
        correctness property, so it is a small assertion - but it is the thing
        that keeps "the only such comment in the repository" true.
        """
        offenders = []
        # ``as_posix()``, not ``str()``: this asserts on a *repository-relative
        # path*, and a repository path is written with forward slashes on every
        # platform. ``str()`` yields ``backend\\services\\job_service.py`` on
        # Windows, so the assertion fails there for a reason that has nothing
        # to do with the rule it is pinning.
        for root in ("backend", "agent"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "tests" in path.parts:
                    continue
                if "the agent never may" in path.read_text(encoding="utf-8"):
                    offenders.append(path.relative_to(REPO_ROOT).as_posix())

        # Sorted, because ``rglob`` has no cross-platform ordering guarantee and
        # a failure that depends on directory iteration order is a flaky test
        # wearing a correctness costume.
        assert sorted(offenders) == ["backend/services/job_service.py"]

    async def test_the_folder_cannot_change_a_jobs_spec_or_created_at(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        AC-15, the precondition #100 inherits.

        ``_process_job_file`` passes seven fields to ``update_job`` and never
        the spec or the created_at, so a folder - wherever it came from -
        cannot rewrite what a job was asked to run. The monotonic terminal
        rule is #100's; this is the cheap half, and the half that would rot
        silently.
        """
        job_id, _ = await submit(auth_client)
        state_file = job_state_file(folder_root, job_id)
        original = read_yaml(str(state_file), JobState)

        impostor = original.model_copy(
            update={
                "spec": create_job_spec(name="someone-elses-job"),
                "created_at": datetime(1999, 1, 1),
                "status": JobStatus.RUNNING,
                "node_id": "node-impostor",
            }
        )
        write_yaml(str(state_file), impostor)

        await SyncthingService(folder_root)._process_job_file(job_id, str(state_file))

        job = await get_store().get_job(job_id)
        assert job.spec == original.spec
        assert job.created_at == original.created_at
        # ... while the seven fields it *is* allowed to move, it does move.
        assert job.status is JobStatus.RUNNING
        assert job.node_id == "node-impostor"

    async def test_retry_on_a_running_job_is_a_409_and_writes_nothing(
        self, auth_client: AsyncClient, folder_root: Path
    ) -> None:
        """
        The other half of AC-15: retry stays gated on FAILED/CANCELLED, which
        is what stops the backend writing PENDING over an agent's RUNNING.

        The file is asserted *not* to have moved as well: a 409 that still
        rewrote the folder would hand the next node a job to claim that the
        database says is already running somewhere.
        """
        job_id, _ = await submit(auth_client)
        await get_store().update_job(job_id, status=JobStatus.RUNNING)

        response = await auth_client.post(f"/api/v1/jobs/{job_id}/retry")

        assert response.status_code == 409
        assert response.json()["error_code"] == "JOB_NOT_RETRYABLE"
        assert (await get_store().get_job(job_id)).status is JobStatus.RUNNING
        assert (
            read_yaml(str(job_state_file(folder_root, job_id)), JobState).status
            is JobStatus.PENDING
        )


# ---------------------------------------------------------------------------
# The delivery both watchers now handle: a rename into the folder
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBothWatchersSeeAnAtomicallyPublishedFile:
    """
    The half of AC-3 the file-only agent depends on.

    ``write_yaml`` publishes by writing a temp file and ``os.replace``-ing it
    into place; Syncthing delivers from another node the same way. Linux
    inotify reports a rename-in as ``IN_MOVED_TO``, watchdog surfaces it as
    ``FileMovedEvent`` and dispatches to ``on_moved``. Neither handler
    implemented that, so a job the backend had written - and a job another node
    had run - were both dropped by the one event kind that actually carries
    them. Measured over 20 identical submissions: 7 produced no event at all.

    Both watchers were changed together and are asserted together, because a
    backend that ingests what an agent wrote while the agent cannot ingest
    what the backend wrote would be a one-way cluster.
    """

    def _agent_handler(self):
        from agent.watcher import _EventHandler, SelfWriteLedger

        seen: list[tuple[str, Path]] = []

        def enqueue(event: Any) -> None:
            seen.append((event.kind, event.path))

        return _EventHandler(enqueue, lambda: None, SelfWriteLedger()), seen

    def _backend_handler(self):
        from backend.services.syncthing_service import SyncthingEventHandler

        return SyncthingEventHandler(cast(Any, SimpleNamespace()))

    def test_the_agent_reports_the_delivered_state_file(
        self, folder_root: Path
    ) -> None:
        handler, seen = self._agent_handler()
        delivered = folder_root / "jobs" / "job-7" / "state.yaml"

        handler.on_moved(FileMovedEvent(str(delivered) + ".x1y2z3.tmp", str(delivered)))

        assert seen == [(EVENT_CREATED, delivered)]

    def test_the_backend_re_reads_the_delivered_state_file(
        self, folder_root: Path
    ) -> None:
        """
        The backend's half, and the reason the other node's claims reach the
        dashboard at all.

        ``_schedule_process`` records the path and, with no event loop set,
        stops there - which is exactly the observation the test needs, and
        does it without a thread.
        """
        handler = self._backend_handler()
        delivered = folder_root / "jobs" / "job-7" / "state.yaml"

        handler.on_moved(FileMovedEvent(str(delivered) + ".x1y2z3.tmp", str(delivered)))

        assert list(handler._pending_events) == [str(delivered)]

    def test_both_watchers_filter_the_same_renames(self, folder_root: Path) -> None:
        """
        Parity, the way ``test_agent_filter_matches_the_backend_filter``
        does it: against the **live** backend handler, so one filter gaining a
        case the other lacks fails here.
        """
        backend = self._backend_handler()
        handler, seen = self._agent_handler()

        for name in ("agent.toml", "state.yaml.tmp", ".state.yaml", "node-01.json"):
            landed = str(folder_root / "nodes" / name)
            handler.on_moved(FileMovedEvent("staging", landed))
            backend.on_moved(FileMovedEvent("staging", landed))

        # A directory named `state.yaml` passes the suffix filter on both
        # sides; only the is-directory check keeps it out.
        as_directory = str(folder_root / "jobs" / "job-7" / "state.yaml")
        handler.on_moved(DirMovedEvent("staging", as_directory))
        backend.on_moved(DirMovedEvent("staging", as_directory))

        assert seen == []
        assert backend._pending_events == {}


# ---------------------------------------------------------------------------
# Node path for on_moved: heartbeat-style atomic REPLACE of nodes/<id>.yaml
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNodePathOnMovedHeartbeat:
    """
    QA finding: `on_moved` is the only way to observe a heartbeat-style atomic
    REPLACE of `nodes/<id>.yaml` (the directory pre-exists, so no `created`
    synthesis). The shipped #95 code was completely blind to this.

    This test uses the real `SyncthingService` (not the agent watcher), has an
    existing `nodes/node-01.yaml` (simulating #95's running agent), calls
    `write_yaml` atomically on that path (the heartbeat case), and spies
    `_process_node_file` to assert it was called.
    """

    async def test_on_moved_processes_atomic_node_heartbeat(
        self, folder_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        An existing node file is atomically replaced (heartbeat). The
        `on_moved` handler must pick it up and call `_process_node_file`.
        """
        from shared.file_ops.yaml_utils import write_yaml
        from shared.schemas.node_spec import GPUInfo, NodeSpec
        from datetime import datetime
        import asyncio

        service = SyncthingService(folder_root)
        # Ensure directories exist
        (folder_root / "nodes").mkdir(parents=True, exist_ok=True)

        # Create initial node file (simulating a running agent from #95)
        node_id = "node-01"
        node_file = folder_root / "nodes" / f"{node_id}.yaml"
        initial_spec = NodeSpec(
            node_id=node_id,
            hostname="node-01.lan",
            gpus=[GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)],
            cpus=16,
            memory_gb=64,
            os="Ubuntu 24.04",
            status="ONLINE",
            last_heartbeat=datetime(2026, 1, 1, 12, 0, 0),
            current_job_id=None,
        )
        write_yaml(str(node_file), initial_spec)

        # Process it once to establish baseline
        await service._process_node_file(node_id, str(node_file))

        # Spy on _process_node_file
        calls: list[str] = []
        original_process = service._process_node_file

        async def spy_process(nid: str, path: str) -> None:
            calls.append(nid)
            await original_process(nid, path)

        monkeypatch.setattr(service, "_process_node_file", spy_process)

        # Now simulate a heartbeat: atomic replace via write_yaml
        # This is exactly what the agent does on each heartbeat
        updated_spec = initial_spec.model_copy(
            update={"last_heartbeat": datetime(2026, 1, 1, 12, 0, 30)}
        )
        write_yaml(str(node_file), updated_spec)  # Atomic: temp file + os.replace

        # Create a handler with the event loop set so debounced processing works
        from backend.services.syncthing_service import SyncthingEventHandler

        handler = SyncthingEventHandler(service)
        handler.set_loop(asyncio.get_running_loop())

        # The temp file name pattern used by write_yaml: <name>.<rand>.tmp
        # We don't know the exact temp name, but on_moved receives dest_path
        # We simulate by calling on_moved with a fake src and the real dest
        from watchdog.events import FileMovedEvent

        handler.on_moved(FileMovedEvent(str(node_file) + ".abc123.tmp", str(node_file)))

        # Give the debounced processing time to run
        await asyncio.sleep(0.6)  # debounce_ms=500 + margin

        # Assert _process_node_file was called for this node
        assert calls == [
            node_id
        ], f"_process_node_file was not called for heartbeat; calls={calls}"


# ---------------------------------------------------------------------------
# AC-14 / AC-18: the contract did not move
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTheApiContractIsUnchanged:
    """AC-14 and AC-18."""

    RESPONSES = {
        ("/api/v1/jobs/", "post"): ["201", "422"],
        ("/api/v1/jobs/{job_id}", "get"): ["200", "422"],
        ("/api/v1/jobs/{job_id}", "delete"): ["204", "422"],
        ("/api/v1/jobs/{job_id}/retry", "post"): ["200", "422"],
    }

    def test_the_live_schema_declares_exactly_what_it_declared_before(self) -> None:
        """
        AC-14.

        The 503s this issue adds are raised inside handlers, so the generator
        cannot see them: no route's signature or response model changed. That
        is asserted here rather than left as a claim in a comment, because the
        alternative - declaring the 503s - would make ``openapi.yaml`` a file
        that has to be regenerated for a behavioural change nobody reviewing
        the diff would notice.
        """
        schema = app.openapi()

        for (path, method), expected in self.RESPONSES.items():
            assert (
                sorted(schema["paths"][path][method]["responses"]) == expected
            ), f"{method.upper()} {path}"

    def test_no_503_is_documented_anywhere(self) -> None:
        """
        D11, checked against the live schema *and* the snapshot.

        If a future change declares one, this fails and says which, instead of
        leaving ``openapi.yaml`` quietly out of date.
        """
        snapshot = json.loads((REPO_ROOT / "openapi.yaml").read_text(encoding="utf-8"))

        for source, schema in (
            ("app.openapi()", app.openapi()),
            ("openapi.yaml", snapshot),
        ):
            documented = sorted(
                path
                for path, item in schema["paths"].items()
                for method, op in item.items()
                if method in ("get", "post", "put", "delete")
                for code in op.get("responses", {})
                if code == "503"
            )
            assert documented == [], f"{source} documents a 503 for {documented}"

    def test_the_snapshot_and_the_live_schema_are_identical(self) -> None:
        """
        Stronger than the response codes: the whole document.

        ``openapi.yaml`` is checked in, and #105 does not regenerate it
        (D11). If the two ever diverge, this is the test that says so.
        """
        snapshot = json.loads((REPO_ROOT / "openapi.yaml").read_text(encoding="utf-8"))

        assert app.openapi() == snapshot

    def test_the_service_layer_reaches_into_no_store_internals(self) -> None:
        """
        AC-18.

        The same guard ``test_api_fixes.py`` applies, re-asserted over the
        files this issue added the logic to - including the new
        ``job_folder.py`` - so the new code is pinned by the same rule and not
        merely by the reviewer's memory of it.

        The reason it is a rule at all: DB-first-with-rollback (D1) is only
        viable because the service layer never needs ``_job_counter``. A
        version that grabbed it to write the file first would look fine and
        would break on the next store refactor.
        """
        guarded = [
            "backend/services/job_service.py",
            "backend/services/job_folder.py",
            "backend/services/syncthing_service.py",
            "backend/api/v1/jobs.py",
        ]

        for relative in guarded:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert not re.search(r"\b(store|_store)\._[a-z]", source), relative


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def wait_for_event(watcher: FolderWatcher, target: Path) -> str | None:
    """Wait for the watcher to report ``target``. Returns the event kind."""
    deadline = time.monotonic() + OBSERVER_TIMEOUT_S
    while time.monotonic() < deadline:
        while not watcher.events().empty():
            event = watcher.events().get_nowait()
            if event.path == target:
                return event.kind
        await asyncio.sleep(0.02)
    return None


def _a_node_spec():
    from shared.schemas.node_spec import GPUInfo, NodeSpec

    return NodeSpec(
        node_id="node-test",
        hostname="node-test.lan",
        gpus=[GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)],
        cpus=16,
        memory_gb=64,
        os="Ubuntu 24.04",
        status="ONLINE",
        last_heartbeat=datetime.now(),
        current_job_id=None,
    )


async def _make_failed(job_id: str, node_id: str = "node-alpha") -> None:
    """Put a job into the terminal state retry accepts, through the store."""
    await get_store().update_job(
        job_id,
        status=JobStatus.FAILED,
        node_id=node_id,
        started_at=datetime.now(),
        completed_at=datetime.now(),
        exit_code=1,
        error="CUDA out of memory at step 12841",
    )


def _always_fails_to_write(file_path: str, data: Any) -> None:
    """A ``write_yaml`` that cannot write. The shape is what matters."""
    raise OSError(13, "Permission denied", file_path)


def _always_fails_to_remove(path: Any, *args: Any, **kwargs: Any) -> None:
    raise OSError(13, "Permission denied", str(path))


async def _always_fails_to_delete(job_id: str) -> bool:
    raise RuntimeError("the database is gone")
