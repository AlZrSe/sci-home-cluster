"""Fixtures for the agent suite.

Two of these exist to stop the suite from contaminating itself:
``restore_process_environment`` undoes the deliberate one-way ``SYNCTHING_ROOT``
publication that ``build_settings`` performs, and ``preserve_root_logging``
restores the root logger's handlers around the tests that call
``configure_logging``. Without them every test after the first would inherit the
first test's state (issue #93 US-5, R2).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from watchdog.observers import Observer

from agent.config import build_settings
from agent.paths import AgentPaths, resolve_paths
from agent.watcher import FolderEvent, FolderWatcher

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeObserver:
    """Stands in for ``watchdog.observers.Observer``; opens no inotify fd."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[str, object]] = []
        self.started = False
        self.stopped = False
        self.joined = False
        self.alive = False
        self.start_error: BaseException | None = None

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        self.scheduled.append((path, handler))
        return object()

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        self.stopped = True
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.joined = True

    def kill_emitter(self) -> None:
        """Simulate the emitter thread dying under a still-running observer."""
        self.alive = False


class RecordingWatcher(FolderWatcher):
    """A ``FolderWatcher`` whose queue the test drives by hand."""

    def __init__(self, paths: AgentPaths) -> None:
        super().__init__(paths, observer_factory=FakeObserver)  # type: ignore[arg-type]

    def emit(self, kind: str, path: Path) -> None:
        self.events().put_nowait(FolderEvent(kind=kind, path=path, observed_at=_NOW()))


def _NOW() -> Any:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def restore_process_environment() -> Iterator[None]:
    """Undo ``build_settings``' one-way publication after every test."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def preserve_root_logging() -> Iterator[None]:
    """Restore the root logger's handlers, level and record factory."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    factory = logging.getLogRecordFactory()
    try:
        yield
    finally:
        root.handlers = handlers
        root.setLevel(level)
        logging.setLogRecordFactory(factory)


@pytest.fixture
def syncthing_root(tmp_path: Path) -> Path:
    root = tmp_path / "syncthing"
    (root / "jobs").mkdir(parents=True)
    (root / "nodes").mkdir(parents=True)
    return root


@pytest.fixture
def agent_settings(syncthing_root: Path):
    return build_settings(
        ["--node-id", "node-test", "--syncthing-root", str(syncthing_root)],
        environ={},
    )


@pytest.fixture
def tmp_paths(syncthing_root: Path) -> AgentPaths:
    return resolve_paths(syncthing_root, "node-test")


@pytest.fixture
def fake_observer() -> list[FakeObserver]:
    """Every ``FakeObserver`` the watcher under test constructed."""
    return []


@pytest.fixture
def observer_factory(fake_observer: list[FakeObserver]) -> Callable[[], Any]:
    def build() -> Any:
        observer = FakeObserver()
        fake_observer.append(observer)
        return observer

    return build


@pytest.fixture
def watcher(
    tmp_paths: AgentPaths, observer_factory: Callable[[], Any]
) -> RecordingWatcher:
    instance = RecordingWatcher(tmp_paths)
    instance._observer_factory = observer_factory  # type: ignore[attr-defined]
    return instance


@pytest.fixture
def scripted_stop() -> asyncio.Event:
    return asyncio.Event()


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Poll ``predicate`` on the running loop until it is true or time runs out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def real_watch_supported() -> bool:
    """Whether this platform can run a real ``watchdog`` observer at all.

    Windows uses ``ReadDirectoryChangesW`` and Linux ``inotify``; a few CI
    sandboxes support neither, and those tests skip rather than fail.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        observer = Observer()
        try:
            observer.schedule(lambda *_a: None, tmp, recursive=True)
            observer.start()
        except (OSError, NotImplementedError, RuntimeError):
            return False
        observer.stop()
        observer.join(timeout=5.0)
    return True


REAL_WATCH_SUPPORTED = real_watch_supported()

__all__ = [
    "REPO_ROOT",
    "FakeObserver",
    "RecordingWatcher",
    "REAL_WATCH_SUPPORTED",
    "wait_for",
    "sys",
]
