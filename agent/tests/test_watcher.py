"""The folder watcher: filters, the ledger, and the two real-observer tests."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from watchdog.events import (
    DirCreatedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from agent.config import CONFIG_FILE_NAME, build_settings
from agent.loop import Agent
from agent.paths import AgentPaths, resolve_paths
from agent.watcher import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_MODIFIED,
    FolderEvent,
    FolderWatcher,
    SelfWriteLedger,
    WatcherDead,
    _EventHandler,
)
from agent.tests.conftest import REAL_WATCH_SUPPORTED, RecordingWatcher
from backend.services.syncthing_service import SyncthingEventHandler
from shared.file_ops.yaml_utils import write_yaml
from shared.schemas.node_spec import NodeSpec

pytestmark = pytest.mark.unit

#: Seven names that between them exercise every branch of the shared filter.
FILTER_NAMES = [
    "state.yaml",
    "state.YAML",
    "state.Yml",
    "agent.toml",
    "state.yaml.tmp",
    ".hidden.yaml",
    "node-01.yaml",
]


def handler_for(watcher: RecordingWatcher) -> _EventHandler:
    return _EventHandler(watcher._enqueue, watcher._discard_pending, watcher.ledger)


def drain(queue: asyncio.Queue[FolderEvent]) -> list[FolderEvent]:
    events: list[FolderEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


# --- the filters ----------------------------------------------------------


def test_tmp_and_dotfiles_are_ignored(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    """``write_yaml``'s ``<name>.<rand>.tmp`` must never re-enter the stream."""
    handler = handler_for(watcher)
    queue = watcher.events()
    for name in ("state.yaml.tmp", ".hidden.yaml", ".state.yaml", "a.tmp"):
        handler.on_created(FileCreatedEvent(str(tmp_paths.nodes_dir / name)))
    assert drain(queue) == []


def test_non_yaml_suffix_is_ignored(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)
    queue = watcher.events()
    for name in (CONFIG_FILE_NAME, "state.yml", "state.json", "state.yaml.bak"):
        handler.on_created(FileCreatedEvent(str(tmp_paths.nodes_dir / name)))
    assert drain(queue) == []


def test_yaml_suffix_is_case_insensitive(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)
    queue = watcher.events()
    for name in ("state.yaml", "state.YAML", "state.Yaml"):
        handler.on_created(FileCreatedEvent(str(tmp_paths.nodes_dir / name)))
    assert len(drain(queue)) == 3


def test_directory_events_are_ignored(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)
    queue = watcher.events()
    handler.on_created(DirCreatedEvent(str(tmp_paths.nodes_dir / "job-1")))
    handler.on_modified(DirCreatedEvent(str(tmp_paths.nodes_dir)))
    assert drain(queue) == []


def test_all_three_event_kinds_are_reported(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)
    queue = watcher.events()
    target = str(tmp_paths.nodes_dir / "node-01.yaml")
    handler.on_created(FileCreatedEvent(target))
    handler.on_modified(FileModifiedEvent(target))
    handler.on_deleted(FileDeletedEvent(target))

    kinds = [event.kind for event in drain(queue)]
    assert kinds == [EVENT_CREATED, EVENT_MODIFIED, EVENT_DELETED]


# --- delivery by rename (issue #105) --------------------------------------
#
# `shared.file_ops.write_yaml` publishes by writing a temp file and
# `os.replace`-ing it into place, and so does Syncthing. On Linux that is
# IN_MOVED_TO, which watchdog surfaces as FileMovedEvent and dispatches to
# `on_moved`. With no `on_moved`, every atomically written state file was
# invisible: measured over 20 identical submissions through
# `POST /api/v1/jobs/`, 7 produced no event at all and 13 produced a
# `created` event only as a side effect of watchdog synthesising events for
# the newly created job directory.


def test_a_file_renamed_into_the_folder_is_reported(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    """The delivery the backend's ``submit_job`` performs."""
    handler = handler_for(watcher)
    temp = tmp_paths.jobs_dir / "job-1" / "state.yaml.a1b2c3.tmp"
    delivered = tmp_paths.jobs_dir / "job-1" / "state.yaml"

    handler.on_moved(FileMovedEvent(str(temp), str(delivered)))

    events = drain(watcher.events())
    assert [event.kind for event in events] == [EVENT_CREATED]
    assert [event.path for event in events] == [delivered]


def test_the_source_of_a_rename_is_never_what_is_reported(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    """
    On a FileMovedEvent ``src_path`` is the *old* name.

    Reporting it would report the temp file, which the filter rejects, and
    which is not the thing that exists when the event is handled.
    """
    handler = handler_for(watcher)
    temp = tmp_paths.jobs_dir / "job-1" / "state.yaml.a1b2c3.tmp"
    delivered = tmp_paths.jobs_dir / "job-1" / "state.yaml"

    handler.on_moved(FileMovedEvent(str(temp), str(delivered)))

    assert all(
        event.path != temp for event in drain(watcher.events())
    ), "the temp file was reported"


def test_a_rename_onto_an_irrelevant_name_is_still_filtered(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    """The filter applies to a destination exactly as it does to a creation."""
    handler = handler_for(watcher)

    for name in ("agent.toml", "state.yaml.tmp", ".state.yaml", "node-01.json"):
        handler.on_moved(
            FileMovedEvent(
                str(tmp_paths.nodes_dir / "staging"),
                str(tmp_paths.nodes_dir / name),
            )
        )

    assert drain(watcher.events()) == []


def test_the_agents_own_rename_is_still_suppressed(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    """
    The ledger records the destination, so the agent's own ``write_yaml`` -
    which also delivers by rename - does not re-enter its control loop.
    """
    handler = handler_for(watcher)
    target = paths_node_file(tmp_paths)
    watcher.ledger.record(target)

    handler.on_moved(FileMovedEvent(str(target) + ".xy12ab.tmp", str(target)))

    assert drain(watcher.events()) == []


def test_a_directory_rename_is_not_reported(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)

    handler.on_moved(
        DirMovedEvent(str(tmp_paths.jobs_dir / "old"), str(tmp_paths.jobs_dir / "new"))
    )
    # Named `state.yaml` on purpose: a directory passes the suffix filter, and
    # only the is-directory check keeps it out of the queue.
    handler.on_moved(
        DirMovedEvent(
            str(tmp_paths.jobs_dir / "job-1" / "staging"),
            str(tmp_paths.jobs_dir / "job-1" / "state.yaml"),
        )
    )

    assert drain(watcher.events()) == []


def test_a_move_without_a_destination_is_ignored(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    """Defensive: a platform event with no ``dest_path`` must not crash."""
    handler = handler_for(watcher)

    handler.on_moved(FileCreatedEvent(str(tmp_paths.nodes_dir / "node-01.yaml")))

    assert drain(watcher.events()) == []


def paths_node_file(paths: AgentPaths) -> Path:
    return paths.node_file


# --- the self-write ledger ------------------------------------------------


def test_self_write_is_not_emitted_as_an_observed_event(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)
    target = tmp_paths.nodes_dir / "node-01.yaml"
    watcher.ledger.record(target)

    handler.on_modified(FileModifiedEvent(str(target)))

    assert drain(watcher.events()) == []


def test_event_for_other_path_is_still_emitted(
    watcher: RecordingWatcher, tmp_paths: AgentPaths
) -> None:
    handler = handler_for(watcher)
    watcher.ledger.record(tmp_paths.nodes_dir / "node-01.yaml")

    handler.on_modified(FileModifiedEvent(str(tmp_paths.nodes_dir / "node-02.yaml")))

    assert len(drain(watcher.events())) == 1


def test_ledger_is_bounded() -> None:
    ledger = SelfWriteLedger(max_remembered=4)
    paths = [Path(f"jobs/job-{index}/state.yaml") for index in range(6)]
    for path in paths:
        ledger.record(path)

    assert len(ledger) == 4
    assert not ledger.is_self_write(paths[0]), "oldest entries are evicted first"
    assert not ledger.is_self_write(paths[1])
    assert ledger.is_self_write(paths[-1])


def test_ledger_max_remembered_is_the_documented_default() -> None:
    assert SelfWriteLedger.MAX_REMEMBERED == 256
    assert len(SelfWriteLedger()) == 0


def test_clear_empties_the_ledger() -> None:
    ledger = SelfWriteLedger()
    first, second = Path("jobs/a/state.yaml"), Path("jobs/b/state.yaml")
    ledger.record(first)
    ledger.record(second)
    assert len(ledger) == 2

    ledger.clear()
    assert len(ledger) == 0
    assert not ledger.is_self_write(first)
    assert not ledger.is_self_write(second)


# --- liveness -------------------------------------------------------------


async def test_probe_liveness_passes_when_observer_is_alive(
    tmp_paths: AgentPaths, observer_factory: Any
) -> None:
    watcher = FolderWatcher(tmp_paths, observer_factory=observer_factory)  # type: ignore[arg-type]
    watcher.start()
    try:
        watcher.probe_liveness()
        assert watcher.is_alive() is True
    finally:
        watcher.stop()


async def test_probe_liveness_raises_when_observer_is_dead(
    tmp_paths: AgentPaths, observer_factory: Any, fake_observer: list[Any]
) -> None:
    watcher = FolderWatcher(tmp_paths, observer_factory=observer_factory)  # type: ignore[arg-type]
    watcher.start()
    fake_observer[0].kill_emitter()
    with pytest.raises(WatcherDead):
        watcher.probe_liveness()
    watcher.stop()


async def test_probe_liveness_raises_when_never_started(tmp_paths: AgentPaths) -> None:
    watcher = FolderWatcher(tmp_paths)
    assert watcher.is_alive() is False
    with pytest.raises(WatcherDead):
        watcher.probe_liveness()


# --- start and stop -------------------------------------------------------


async def test_start_schedules_jobs_and_nodes(
    tmp_paths: AgentPaths, observer_factory: Any, fake_observer: list[Any]
) -> None:
    watcher = FolderWatcher(tmp_paths, observer_factory=observer_factory)  # type: ignore[arg-type]
    watcher.start()
    try:
        scheduled = [path for path, _handler in fake_observer[0].scheduled]
        assert scheduled == [str(tmp_paths.jobs_dir), str(tmp_paths.nodes_dir)]
        assert fake_observer[0].started is True
    finally:
        watcher.stop()


async def test_start_with_absent_folder_raises_a_clear_error(
    syncthing_root: Path,
) -> None:
    missing = resolve_paths(syncthing_root / "not-there", "node-01")
    watcher = FolderWatcher(missing)
    with pytest.raises(FileNotFoundError) as excinfo:
        watcher.start()
    assert str(missing.jobs_dir) in str(excinfo.value)


async def test_stop_is_safe_to_call_twice(
    tmp_paths: AgentPaths, observer_factory: Any, fake_observer: list[Any]
) -> None:
    watcher = FolderWatcher(tmp_paths, observer_factory=observer_factory)  # type: ignore[arg-type]
    watcher.start()
    watcher.stop()
    watcher.stop()
    assert fake_observer[0].stopped is True
    assert fake_observer[0].joined is True
    assert watcher.is_alive() is False


async def test_stop_before_start_is_safe(tmp_paths: AgentPaths) -> None:
    FolderWatcher(tmp_paths).stop()


async def test_watcher_start_failure_is_not_fatal(
    tmp_paths: AgentPaths, observer_factory: Any
) -> None:
    """A watcher that refuses to start is recorded and retried, not fatal."""
    attempts = 0

    def exploding() -> Any:
        nonlocal attempts
        attempts += 1
        observer = observer_factory()
        observer.start_error = OSError("inotify exhausted")
        return observer

    watcher = FolderWatcher(tmp_paths, observer_factory=exploding)  # type: ignore[arg-type]
    settings = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(tmp_paths.root)],
        environ={},
    )
    agent = Agent(settings, watcher=watcher)

    raised = 0
    for _ in range(3):
        try:
            await agent._check_folder()
        except OSError:
            raised += 1

    assert attempts == 3
    assert raised == 3, "the failure is raised so the supervisor records it"
    assert agent._watcher_started is False
    watcher.stop()


def test_unwritable_folder_is_reported_not_written_to(
    syncthing_root: Path, monkeypatch: Any
) -> None:
    """``os.access`` answers "is it writable"; the agent writes nothing to find out."""
    settings = build_settings(
        ["--node-id", "node-01", "--syncthing-root", str(syncthing_root)],
        environ={},
    )
    agent = Agent(settings)
    monkeypatch.setattr("agent.loop.os.access", lambda *_a, **_k: False)

    before = sorted(p.name for p in syncthing_root.iterdir())
    assert "not writable" in str(agent._describe_folder_problem())
    assert sorted(p.name for p in syncthing_root.iterdir()) == before


def test_agent_filter_matches_the_backend_filter() -> None:
    """R5: the two filters must observe the same file set.

    Compares the agent's filter against the backend's **live** one, so one
    gaining a case the other lacks fails here rather than in production.
    """
    backend = SyncthingEventHandler(cast(Any, SimpleNamespace()))
    for name in FILTER_NAMES:
        assert _EventHandler._is_relevant_file(name) == backend._is_relevant_file(
            name
        ), name


def test_agent_filter_agrees_on_the_derived_config_path(syncthing_root: Path) -> None:
    backend = SyncthingEventHandler(cast(Any, SimpleNamespace()))
    config = syncthing_root / "nodes" / CONFIG_FILE_NAME
    assert _EventHandler._is_relevant_file(str(config)) is False
    assert backend._is_relevant_file(str(config)) is False


# --- real-observer integration -------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not REAL_WATCH_SUPPORTED, reason="no real filesystem observer here")
async def test_write_yaml_through_a_real_watcher_is_not_re_entered(
    syncthing_root: Path,
) -> None:
    """The only test that opens a real watch."""

    paths = resolve_paths(syncthing_root, "node-01")
    watcher = FolderWatcher(paths)
    watcher.start()
    try:
        spec = NodeSpec(
            node_id="node-01",
            hostname="real-host",
            gpus=[],
            cpus=1,
            memory_gb=1,
            os="linux",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
            current_job_id=None,
        )
        for _ in range(3):
            watcher.ledger.record(paths.node_file)
            write_yaml(str(paths.node_file), spec)

        await asyncio.sleep(1.0)
        while not watcher.events().empty():
            await asyncio.sleep(0.1)

        assert watcher.events().qsize() == 0
    finally:
        watcher.stop()


@pytest.mark.integration
@pytest.mark.skipif(not REAL_WATCH_SUPPORTED, reason="no real filesystem observer here")
async def test_real_watcher_reports_a_foreign_write(syncthing_root: Path) -> None:
    """The control: without it, "zero events" would also describe a deaf watcher."""

    paths = resolve_paths(syncthing_root, "node-01")
    watcher = FolderWatcher(paths)
    watcher.start()
    try:
        foreign = paths.nodes_dir / "node-02.yaml"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and watcher.events().qsize() == 0:
            foreign.write_text("node_id: node-02\n", encoding="utf-8")
            await asyncio.sleep(0.1)

        assert watcher.events().qsize() >= 1, (
            "the real watcher saw nothing at all, so the no-re-entry test proves "
            "nothing"
        )
    finally:
        watcher.stop()
