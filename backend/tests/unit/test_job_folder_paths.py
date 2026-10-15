"""
The backend's folder resolver and ``shared.file_ops.path_utils`` must agree
(issue #105, AC-10).

Two resolvers for one folder is the bug class of #34 (two SQLite files for one
database) and #58 (``.env`` found from one CWD and not another), and this file
is the tripwire that keeps them from becoming a real split: the backend's
watcher, ``/health`` and ``/syncthing/status`` all read
``settings.SYNCTHING_ROOT`` while ``get_job_state_file`` reads
``os.environ["SYNCTHING_ROOT"]``, and those two disagree whenever the root is
set in ``.env`` alone. The backend writes on the settings value; if the two
resolvers ever disagree about where that is, the backend writes into a folder
nothing is watching.

The direct analogue is ``agent/tests/test_hardware.py:531-543``, which pins
``AgentPaths.node_file`` against ``get_node_state_file`` for the same reason.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.core.config import settings
from backend.services.job_folder import (
    JOB_STATE_FILE_NAME,
    JOBS_DIR_NAME,
    is_inside_jobs_root,
    job_dir,
    job_state_file,
    jobs_dir,
    resolve_root,
)
from shared.file_ops.path_utils import (
    get_job_directory,
    get_job_state_file,
    get_jobs_directory,
)

JOB_IDS = ["job-1", "job-7", "job-1051", "job-999999"]


class TestTheTwoResolversAgree:
    """AC-10: one folder, one convention."""

    @pytest.mark.parametrize("job_id", JOB_IDS)
    def test_job_state_file_matches_get_job_state_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_id: str
    ) -> None:
        root = tmp_path / "syncthing"
        root.mkdir()
        # Both channels pointed at the same directory: this is the whole
        # point of the test. If either resolver preferred its own source,
        # this would stop holding.
        monkeypatch.setenv("SYNCTHING_ROOT", str(root))
        monkeypatch.setattr(settings, "SYNCTHING_ROOT", str(root))

        assert job_state_file(root, job_id) == get_job_state_file(job_id)
        assert get_job_state_file(job_id) == (
            Path(settings.SYNCTHING_ROOT) / "jobs" / job_id / "state.yaml"
        )

    @pytest.mark.parametrize("job_id", JOB_IDS)
    def test_job_dir_matches_get_job_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_id: str
    ) -> None:
        root = tmp_path / "syncthing"
        root.mkdir()
        monkeypatch.setenv("SYNCTHING_ROOT", str(root))

        assert job_dir(root, job_id) == get_job_directory(job_id)

    def test_jobs_dir_matches_get_jobs_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "syncthing"
        root.mkdir()
        monkeypatch.setenv("SYNCTHING_ROOT", str(root))

        assert jobs_dir(root) == get_jobs_directory()

    def test_a_str_and_a_path_root_resolve_the_same(self, tmp_path: Path) -> None:
        """
        ``settings.SYNCTHING_ROOT`` is a ``str``; every caller in the codebase
        hands it over as one. A resolver that only worked for ``Path`` would
        pass the tests above and fail in production.
        """
        root = tmp_path / "syncthing"

        assert job_state_file(str(root), "job-1") == job_state_file(root, "job-1")

    def test_a_relative_root_is_resolved_to_an_absolute_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        ``normalize_path`` resolves a relative root against the process CWD,
        which is what makes the resolver agree with ``path_utils`` rather than
        quietly producing a different folder.
        """
        monkeypatch.setenv("SYNCTHING_ROOT", "relative-root")
        monkeypatch.chdir(tmp_path)

        resolved = job_state_file("relative-root", "job-1")

        assert resolved.is_absolute()
        assert resolved == get_job_state_file("job-1")
        assert resolved == (
            Path(tmp_path).resolve() / "relative-root" / "jobs" / "job-1" / "state.yaml"
        )

    def test_the_convention_literals_are_pinned(self) -> None:
        """
        ``JOBS_DIR_NAME`` and ``JOB_STATE_FILE_NAME`` are only worth having if
        they are the ones the rest of the system uses - a rename of either
        would otherwise silently point the backend at a different layout.
        """
        assert JOBS_DIR_NAME == "jobs"
        assert JOB_STATE_FILE_NAME == "state.yaml"

    def test_the_resolver_never_consults_the_environment(self, tmp_path: Path) -> None:
        """
        The defect this module exists to avoid (C8), asserted directly.

        ``get_syncthing_root`` has no injection point: it reads
        ``os.environ`` and nothing else. If ``job_state_file`` ever grew the
        same habit, the backend would start writing into the environment's
        folder while its own watcher watches the settings one - and both would
        be reachable in a deployment that sets the root in ``.env``.
        """
        os.environ.pop("SYNCTHING_ROOT", None)

        resolved = job_state_file(tmp_path / "elsewhere", "job-1")

        assert resolved == (tmp_path / "elsewhere" / "jobs" / "job-1" / "state.yaml")


class TestRemoveStaysInsideTheJobsFolder:
    """§5.4 step 2: the path is checked before anything is removed."""

    def test_a_job_directory_is_inside(self, tmp_path: Path) -> None:
        assert is_inside_jobs_root(tmp_path, job_dir(tmp_path, "job-1")) is True

    def test_the_jobs_root_itself_is_inside(self, tmp_path: Path) -> None:
        assert is_inside_jobs_root(tmp_path, jobs_dir(tmp_path)) is True

    @pytest.mark.parametrize(
        "escaping",
        [
            "job-1/../../..",
            "..",
            "../other",
            "job-1/../../elsewhere",
            "/etc",
        ],
    )
    def test_a_path_outside_jobs_is_refused(
        self, tmp_path: Path, escaping: str
    ) -> None:
        assert is_inside_jobs_root(tmp_path, jobs_dir(tmp_path) / escaping) is False

    @pytest.mark.parametrize("traversal", [".", "job-1/../job-2", "./job-1"])
    def test_a_traversal_that_lands_inside_is_accepted(
        self, tmp_path: Path, traversal: str
    ) -> None:
        """
        ``..`` is resolved, not string-matched, so a spelling that lands back
        inside ``jobs/`` is admitted and one that lands outside is refused.
        That is the property that matters; a ``startswith`` implementation
        fails both halves of it.
        """
        assert is_inside_jobs_root(tmp_path, jobs_dir(tmp_path) / traversal) is True

    def test_a_sibling_directory_is_refused(self, tmp_path: Path) -> None:
        """
        ``<root>/.agent/<node_id>`` is where an agent keeps its own state and
        it is a sibling of ``jobs/``. It must never be reachable from a job
        id (AC-12).
        """
        agent_state = tmp_path / ".agent" / "node-01"

        assert is_inside_jobs_root(tmp_path, agent_state) is False

    def test_a_prefix_match_is_not_enough(self, tmp_path: Path) -> None:
        """
        ``jobs-backup/`` starts with ``jobs`` but is not inside it, which is
        the failure mode a ``str.startswith`` implementation would have.
        """
        assert (
            is_inside_jobs_root(tmp_path, tmp_path / "jobs-backup" / "job-1") is False
        )


class TestRootResolution:
    def test_a_tilde_root_expands(self) -> None:
        """
        ``normalize_path`` raises ``TypeError`` for a ``Path`` carrying a
        ``~/`` prefix (issue #93 D6/C16), so the tilde is expanded by
        ``pathlib`` first - exactly as ``agent/paths.py`` does.
        """
        assert resolve_root("~/syncthing") == Path.home() / "syncthing"

    def test_resolution_is_idempotent(self, tmp_path: Path) -> None:
        once = resolve_root(tmp_path)

        assert resolve_root(once) == once
