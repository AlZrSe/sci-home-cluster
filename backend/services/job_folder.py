"""
The backend's half of the Syncthing folder layout (issue #105).

Every path is derived from an explicit ``root`` argument rather than from
``shared.file_ops.path_utils.get_job_state_file()``: those helpers read
``os.environ["SYNCTHING_ROOT"]`` and have no injection point (defect C8 of
``docs/specs/93-agent-skeleton.md``), so a backend caller could only ever
write into the environment's folder. That is not the folder the backend
watches - the watcher, ``/health`` and ``/syncthing/status`` all read
``settings.SYNCTHING_ROOT``, and those two channels disagree whenever the
root is set in ``.env`` only. Two roots for one folder is the same class of
defect as #34 (two SQLite files for one database).

``agent/paths.py`` exists for exactly the same reason and is the model for
this module; ``test_backend_job_folder_paths.py`` pins the two conventions
together so neither can drift into a real split.

Only *root resolution* is new here. ``ensure_directory`` and ``write_yaml``
still come from ``shared/``, so the folder layout stays one convention and
the write stays atomic (temp file, then ``os.replace``).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from shared.file_ops.path_utils import ensure_directory, normalize_path
from shared.file_ops.yaml_utils import write_yaml
from shared.schemas.job_state import JobState

logger = logging.getLogger(__name__)

#: Directory holding one sub-directory per job. Mirrors
#: ``get_jobs_directory`` in ``shared/file_ops/path_utils.py``.
JOBS_DIR_NAME = "jobs"

#: The one file per job, written by the backend on create and on retry and by
#: the agent on claim and on terminal state. Mirrors ``get_job_state_file``.
JOB_STATE_FILE_NAME = "state.yaml"


def resolve_root(value: str | Path) -> Path:
    """Expand ``~``, make ``value`` absolute and collapse ``.``/``..``.

    ``shared.file_ops.path_utils.normalize_path`` subscripts a ``Path`` for
    any ``~/...`` input and raises ``TypeError`` (issue #93 D6/C16), so the
    tilde is expanded by ``pathlib`` first. That bug is out of scope here;
    this module simply does not depend on it, exactly as ``agent/paths.py``
    does not.

    The ``resolve()`` is what makes the layout functions safe to compare
    against each other: without it two spellings of the same directory - one
    carrying ``..`` - are two different paths. It is also idempotent, so
    ``resolve_root(resolve_root(x)) == resolve_root(x)``.
    """
    return normalize_path(Path(value).expanduser()).resolve()


def jobs_dir(root: str | Path) -> Path:
    """``<root>/jobs``."""
    return resolve_root(root) / JOBS_DIR_NAME


def job_dir(root: str | Path, job_id: str) -> Path:
    """``<root>/jobs/<job_id>``.

    The directory name is the primary key: the ``job_id`` a submission is
    given and the ``job_id`` the file carries are the same string, which is
    what stops the backend from writing one job's state into another's
    directory.
    """
    return jobs_dir(root) / job_id


def job_state_file(root: str | Path, job_id: str) -> Path:
    """``<root>/jobs/<job_id>/state.yaml``.

    Never returns ``None``. That is the point of resolving the root here
    rather than through ``get_job_state_file``: ``settings.SYNCTHING_ROOT``
    is a ``str`` with a documented default (``config.py``), so "unconfigured"
    is not a state this path can be in, and every caller is spared a
    ``None`` check that could only ever be dead.
    """
    return job_dir(root, job_id) / JOB_STATE_FILE_NAME


def is_inside_jobs_root(root: str | Path, path: Path) -> bool:
    """Whether ``path`` resolves to somewhere at or under ``<root>/jobs``.

    Checked before anything is removed from disk. Job ids are ``job-\\d+``
    today, so this is belt and braces - but it is the only thing standing
    between a malformed id and an ``rmtree`` that escapes the folder.

    Both sides go through ``Path.resolve()``, which ``normalize_path`` alone
    does not do for an already-absolute path: without it ``jobs/job-1/../..``
    is compared as a literal string and reads as *inside* ``jobs``.
    """
    resolved = resolve_root(path)
    jobs = resolve_root(root) / JOBS_DIR_NAME
    return resolved == jobs or jobs in resolved.parents


def write_job_state(root: str | Path, state: JobState) -> Path:
    """Publish ``state`` as ``<root>/jobs/<state.job_id>/state.yaml``.

    ``write_yaml`` is atomic - temp file, then ``os.replace`` - so a reader
    that arrives mid-write sees either the previous file or the complete new
    one. That matters more here than elsewhere: this module has two other
    readers (the agent's watcher and the backend's own), neither of which
    takes a lock, and a truncated document is one they both drop without a
    visible failure.

    The directory is created explicitly so that "the folder is not there"
    and "the folder is not writable" fail here, where the caller can turn
    them into a 503, rather than deep inside ``write_yaml``.

    The resolved path is logged at INFO on every success, deliberately: the
    first question when a submitted job does not appear on a node is whether
    this process wrote the file that node's watcher is looking at, and a log
    line is the only place that answer can come from. Same reasoning, and the
    same shape, as ``agent/hardware.py:write_node_spec``.
    """
    target = job_state_file(root, state.job_id)
    ensure_directory(target.parent)
    write_yaml(str(target), state)
    logger.info("published job %s at %s", state.job_id, target)
    return target


def remove_job_dir(root: str | Path, job_id: str) -> bool:
    """Remove ``<root>/jobs/<job_id>`` and everything under it.

    The whole directory goes, not just ``state.yaml``: leaving the directory
    behind leaks it, and the per-job scratch space an agent keeps inside it
    survives a job the operator deleted.

    Returns ``False`` - not an exception - when the directory was already
    absent, because that is the state the caller asked for. A genuine
    ``OSError`` propagates: the caller turns it into a 503 and leaves the
    database row alone, which is the only outcome in which the folder and
    the database still agree.
    """
    directory = job_dir(root, job_id)
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        logger.info("job directory %s was already absent", directory)
        return False
    logger.info("removed job directory %s", directory)
    return True
