"""
Job service layer containing business logic for job management.

Submission, retry and delete all live here rather than in
`backend/api/v1/jobs.py` because that route file is excluded from mypy
(`pyproject.toml`) and the folder side of these three operations is exactly
the part that must be type checked: it is the only thing standing between a
submitted job and a cluster that will never run it (issue #105).
"""

from typing import List, Optional, Tuple, Callable
import asyncio
import logging
from datetime import datetime
from shared.schemas.job_state import JobState
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_status import JobStatus
from shared.schemas.job_metrics import JobMetrics
from shared.schemas.log_entry import LogEntry
from backend.core.config import settings
from backend.core.errors import (
    job_id_collision,
    job_not_found,
    job_not_retryable,
    syncthing_unavailable,
)
import time
import uuid

from backend.services.job_folder import (
    command_file,
    is_inside_jobs_root,
    job_dir,
    jobs_dir,
    remove_job_dir,
    write_job_state,
)
from shared.schemas.job_command import JobCommand, JobCommandType
from backend.store import get_store
from backend.store.database_store import DatabaseStore

logger = logging.getLogger(__name__)


class JobService:
    def __init__(self):
        # Initialize the store
        self._store: DatabaseStore = get_store()

    async def list_jobs(
        self,
        status: Optional[str] = None,
        node_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[JobState], int]:
        """List jobs with filtering and pagination."""
        return await self._store.list_jobs(
            status=status, node_id=node_id, search=search, limit=limit, offset=offset
        )

    async def submit_job(self, job_data: JobSpec) -> JobState:
        """Create a job and publish it to the Syncthing folder.

        Database first, file second, with a compensating rollback (issue
        #105, D1). The order is forced by the id: `create_job` allocates
        `job-<counter>` internally, and the counter is a private attribute of
        the store that the service and router layers are forbidden from
        touching (`test_api_fixes.py::TestNoPrivateStoreAccess`). So the id
        cannot be known before the row exists, and the file cannot be written
        before the row does.

        The compensation is the point: either the row and the file both exist
        or neither does. A job in the database and not in the folder is
        precisely the defect this issue fixes, and a partial success would be
        that same defect one layer further from the operator.
        """
        created: JobState = await self._store.create_job(job_data)
        root = settings.SYNCTHING_ROOT
        directory = job_dir(root, created.job_id)

        # Clobber guard, create only. The id was just allocated from a counter
        # synced to MAX(job-N) in the *database*, so a directory already
        # sitting at this path means the two disagree - typically a wiped
        # database against a surviving folder. Writing here would overwrite
        # another job's state.yaml and the backend's own watcher would then
        # apply this job's fields to that other job's row. Retry deliberately
        # does not run this: the directory is supposed to be there, and a
        # retried job whose directory was lost is exactly what retry repairs.
        if directory.exists():
            # The directory pre-exists (belongs to a different job from a
            # previous run). Roll back ONLY the database row we just created;
            # do NOT touch the pre-existing directory.
            await self._rollback_created_job_db_only(created.job_id)
            raise job_id_collision(created.job_id, str(directory))

        try:
            write_job_state(root, created)
        except OSError as exc:
            await self._rollback_created_job(created.job_id)
            raise syncthing_unavailable(
                f"Job {created.job_id} was rolled back and not queued: the "
                f"Syncthing folder at {directory} could not be written "
                f"({exc}). Nothing was submitted, so no node will run it."
            ) from exc

        try:
            from backend.core.metrics import record_job_submitted

            record_job_submitted()
        except Exception:
            pass

        return created

    async def _rollback_created_job(self, job_id: str) -> None:
        """Undo `create_job` after its folder write failed.

        Swallows the failure on purpose, because the caller is about to
        return 503 either way and losing that 503 to a second exception
        would replace an honest answer with a 500. It is logged at ERROR
        with the `job_id`, which is the only thing left that can be acted on:
        if this rollback fails the row survives with no file, and a greppable
        orphan is more honest than a silent one. Reconciling that is #103's
        job.

        Also removes the job directory if it was created but the write failed,
        to avoid leaving an empty directory that poisons the job id (a reissue
        would hit the 409 clobber guard with a confusing message).
        """
        root = settings.SYNCTHING_ROOT
        directory = job_dir(root, job_id)
        try:
            await self._store.delete_job(job_id)
        except Exception:
            logger.error(
                "compensation failed: job %s is in the database with no file "
                "in the Syncthing folder. Delete it by hand, or let #103 "
                "reconcile the two.",
                job_id,
                exc_info=True,
            )
        # Best-effort removal of the directory (may be empty or partially written).
        # This prevents the "empty directory residual" that would cause a 409
        # JOB_ID_COLLISION on reissue with a misleading message.
        try:
            remove_job_dir(root, job_id)
        except OSError:
            # If we can't remove it, the row is already gone and the folder
            # will be reconciled by #103. Log but don't raise - the 503 is
            # the honest answer the caller expects.
            logger.error(
                "compensation failed: could not remove job directory %s after "
                "failed write. Manual cleanup or #103 reconciliation needed.",
                directory,
                exc_info=True,
            )

    async def _rollback_created_job_db_only(self, job_id: str) -> None:
        """Undo `create_job` for the clobber guard case.

        The directory pre-exists (belongs to a different job from a previous
        run), so we only roll back the database row we just created. We do
        NOT remove the directory, as it contains another job's state.yaml.
        """
        try:
            await self._store.delete_job(job_id)
        except Exception:
            logger.error(
                "compensation failed (clobber guard): job %s is in the database "
                "with a pre-existing directory in the Syncthing folder. The "
                "directory was left intact. Delete the row by hand, or let #103 "
                "reconcile the two.",
                job_id,
                exc_info=True,
            )

    async def get_job(self, job_id: str) -> Optional[JobState]:
        """Get a job by ID."""
        return await self._store.get_job(job_id)

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job from the database *and* from the Syncthing folder.

        Folder first, row second, and that ordering is load-bearing (issue
        #105, D8). A `state.yaml` whose database row is gone is re-created by
        the next scan: `_process_job_file` finds no existing job and calls
        `create_job_with_id`. Deleting the row first therefore resurrects the
        job on the next scan, with its metrics and logs gone and its terminal
        fields overwritten by a stale file. Delete-the-folder-first cannot do
        that, because the next scan finds nothing to re-ingest.

        The consequences of that deletion are asymmetric, and deliberately so:
        **the backend may delete a job directory; the agent never may.**
        Deleting `state.yaml` routes straight to `store.delete_job`, which
        purges the job's `gpu_metrics`, `cpu_metrics` and `log_entries` rows
        (`backend/store/database_store.py:474-482`). The agent's only record
        of a job is that file, so an agent that deleted its own job's state
        would destroy the job, its history and its telemetry (#92 trap 2,
        #100 hazard 3). This is the only site in the repository that can act
        on the rule, so the rule is stated here rather than only in
        `docs/specs/105-job-submission-folder.md` §5.5; a test asserts the
        comment is still present.

        The 404 is decided here, from the pre-read, rather than from
        `delete_job`'s return value: after this change a `False` most often
        means the running watcher won the race and deleted the row first,
        which is the end state the client asked for. That delete returns 204.
        """
        job: Optional[JobState] = await self._store.get_job(job_id)
        if job is None:
            raise job_not_found(job_id)

        root = settings.SYNCTHING_ROOT
        directory = job_dir(root, job_id)
        if not is_inside_jobs_root(root, directory):
            # Unreachable for a `job-\d+` id, and deliberately fatal rather
            # than merely logged: the next statement removes this path.
            raise syncthing_unavailable(
                f"Refusing to remove {directory}: it is not inside "
                f"{jobs_dir(root)}. This is a bug, not a configuration "
                "problem."
            )

        try:
            removed = remove_job_dir(root, job_id)
        except OSError as exc:
            # Row and file stay together, which is the only state this method
            # can leave behind. Notably the job is NOT resurrected here: a
            # 503 is an honest "nothing changed", not a partial success.
            raise syncthing_unavailable(
                f"Job {job_id} was not deleted: the Syncthing folder at "
                f"{directory} could not be removed ({exc}). The job is "
                "unchanged, in the database and in the folder."
            ) from exc

        deleted = await self._store.delete_job(job_id)
        logger.info(
            "deleted job %s (directory removed: %s, row deleted: %s)",
            job_id,
            removed,
            deleted,
        )
        return True

    async def get_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """Get job GPU/CPU metrics."""
        return await self._store.get_job_metrics(job_id)

    async def get_job_logs(self, job_id: str) -> Optional[List[LogEntry]]:
        """Get job logs."""
        return await self._store.get_job_logs(job_id)

    async def retry_job(self, job_id: str) -> JobState:
        """Retry a failed/cancelled job and re-publish it to the folder.

        Retry is submission's second half (issue #105, D4): the job already
        has a directory, and leaving it there is what made a retried job look
        healthy in the dashboard while remaining invisible to every node.
        The write here is unconditional and does *not* run the create-time
        clobber guard - the directory is supposed to exist, and a retried
        job whose directory was lost (written before this change landed, or
        after a folder reset) has to come back. Retry is the repair path.

        The reset touches only fields the backend owns, and only in the
        directions that make a job newly claimable: `status` back to
        `PENDING`, `node_id` cleared, the terminal fields cleared, and
        `retry_count` incremented. `spec` and `created_at` are untouched -
        the folder can never change a job's command after submission.
        """
        job: Optional[JobState] = await self._store.get_job(job_id)
        if not job:
            raise job_not_found(job_id)
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise job_not_retryable(job_id, job.status.value)
        # Reset the job to PENDING
        updated_job: Optional[JobState] = await self._store.update_job(
            job_id,
            status=JobStatus.PENDING,
            node_id=None,  # When retried, it's not assigned to a node yet
            started_at=None,
            completed_at=None,
            exit_code=None,
            error=None,
            retry_count=job.retry_count + 1,
        )
        if updated_job is None:
            # The row went away between the pre-read and the update. Nothing
            # was written to the folder, so there is nothing to roll back.
            raise job_not_found(job_id)

        try:
            write_job_state(settings.SYNCTHING_ROOT, updated_job)
        except OSError as exc:
            await self._restore_failed_snapshot(job)
            raise syncthing_unavailable(
                f"Job {job_id} was left as {job.status.value} and not "
                f"re-queued: the Syncthing folder at "
                f"{job_dir(settings.SYNCTHING_ROOT, job_id)} could not be "
                f"written ({exc})."
            ) from exc

        return updated_job

    async def _restore_failed_snapshot(self, snapshot: JobState) -> None:
        """Put `snapshot` back after the retry's folder write failed.

        The exact mirror of the reset above rather than an approximation: the
        job must go back to the state the operator was looking at when they
        pressed retry, including its `retry_count`, or a retry that failed
        to publish would silently consume an attempt.

        Same discipline as `_rollback_created_job`: a failure here is logged
        at ERROR with the `job_id` and swallowed, so the caller still answers
        503 instead of masking it with a 500.
        """
        try:
            await self._store.update_job(
                snapshot.job_id,
                status=snapshot.status,
                node_id=snapshot.node_id,
                started_at=snapshot.started_at,
                completed_at=snapshot.completed_at,
                exit_code=snapshot.exit_code,
                error=snapshot.error,
                retry_count=snapshot.retry_count,
            )
        except Exception:
            logger.error(
                "compensation failed: job %s is PENDING in the database with "
                "no file in the Syncthing folder. Delete it by hand, or let "
                "#103 reconcile the two.",
                snapshot.job_id,
                exc_info=True,
            )

    async def cancel_job(
        self, job_id: str, issued_by: str = "localhost_user"
    ) -> Optional[JobState]:
        """Cancel a running/pending job.

        Writes a command file *before* updating the row (issue #106, D4): if the
        file cannot be written the operator gets a 503 and the job stays
        genuinely RUNNING. A row that says CANCELLED with nothing delivered is
        the exact defect this issue exists to remove.
        """
        job: Optional[JobState] = await self._store.get_job(job_id)
        if not job:
            return None
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return None
        # Mint the command id once: cancel-<epoch_ms>-<hex6>. Sortable and
        # readable in an `ls` (issue #106 D8).
        now = datetime.now()
        command_id = f"cancel-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        command = JobCommand(
            command_id=command_id,
            command=JobCommandType.CANCEL,
            issued_at=now,
            issued_by=issued_by,
        )
        root = settings.SYNCTHING_ROOT
        target = command_file(root, job_id, command_id)
        try:
            from shared.file_ops.path_utils import ensure_directory
            from shared.file_ops.yaml_utils import write_yaml

            ensure_directory(target.parent)
            write_yaml(str(target), command)
        except OSError:
            raise syncthing_unavailable(
                f"could not write command file {target}: Syncthing folder "
                f"unavailable"
            )
        # Only now update the row. Step 4 is unchanged from before.
        updated_job: Optional[JobState] = await self._store.update_job(
            job_id,
            status=JobStatus.CANCELLED,
            completed_at=now,
            exit_code=-1,  # Indicates cancelled
        )
        return updated_job

    async def start_log_stream(
        self, job_id: str, on_line: Callable[[str], None]
    ) -> Optional[asyncio.Task]:
        """
        Start simulating log streaming for a job.

        Returns the stream task, or None when the stream is unavailable
        because demo data is disabled (issue #35).
        """
        task: Optional[asyncio.Task] = await self._store.start_log_stream(
            job_id, on_line
        )
        return task

    def log_stream_available(self) -> bool:
        """Whether the store can produce any streamed log lines."""
        return self._store.log_stream_available()

    async def stop_log_stream(self, job_id: str) -> None:
        """Stop the log stream for a job."""
        await self._store.stop_log_stream(job_id)

    def subscribe_to_log_stream(self, job_id: str, on_line: Callable[[str], None]):
        """Subscribe to log stream updates for a job."""
        self._store.subscribe_to_log_stream(job_id, on_line)

    def unsubscribe_from_log_stream(self, job_id: str, on_line: Callable[[str], None]):
        """Unsubscribe from log stream updates for a job."""
        self._store.unsubscribe_from_log_stream(job_id, on_line)
