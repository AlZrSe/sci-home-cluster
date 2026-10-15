"""Fixtures for the agent suite.

Two of these exist to stop the suite from contaminating itself:
``restore_process_environment`` undoes the deliberate one-way ``SYNCTHING_ROOT``
publication that ``build_settings`` performs, and ``preserve_root_logging``
restores the root logger's handlers around the tests that call
``configure_logging``. Without them every test after the first would inherit the
first test's state (issue #93 US-5, R2).

A third exists because a test failure is not the worst thing this suite can do
to itself: the agent half of it used to signal *pytest*, and a signal delivered
at ``SIG_DFL`` kills the interpreter mid-file with no traceback and no summary
(issue #114). ``SignalSafetyGuard`` turns that back into a red test.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from agent.config import build_settings
from agent.paths import AgentPaths, resolve_paths
from agent.watcher import FolderEvent, FolderWatcher

from shared.testing.signal_guard import (
    GUARDED_SIGNALS,
    SIGNAL_GUARD,
    SignalSafetyGuard,
    signal_name,
)

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
def signal_safety_guard() -> Iterator[SignalSafetyGuard]:
    """Re-arm the signal safety guard for every test (issue #114 D4).

    ``Agent.run()`` installs its own SIGTERM/SIGINT handlers mid-test and never
    removes them, so a guard armed once at session start would be gone by the
    time the second signal test finished. Re-arming per test means a stray
    signal is recorded again from the next boundary on.
    """
    SIGNAL_GUARD.install()
    yield SIGNAL_GUARD


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


#: Generous ceiling for :func:`wait_until`. Long enough that a loaded machine
#: misses the deadline only when the property is genuinely false, short enough
#: that a real failure is reported in seconds rather than minutes.
WAIT_UNTIL_TIMEOUT_S: float = 10.0


async def wait_until(
    predicate: Callable[[], bool],
    message: str,
    *,
    timeout: float = WAIT_UNTIL_TIMEOUT_S,
) -> None:
    """Block until ``predicate`` holds; fail the test with ``message`` if it never does.

    The load-safe replacement for "sleep a fixed fraction of a second, then
    assert that something happened ``n`` times". A fixed sleep and a count
    assertion are two separate bets: the sleep must outlast the interval *and*
    the event loop must get enough scheduling slots to deliver the ticks. Under
    load a 10 ms-interval task can be starved for the whole window, and the
    count assertion then fails for a supervisor that is behaving correctly.

    Waiting for the property itself removes the first bet. The caller keeps its
    own assertion on the same property afterwards, so a genuine failure is still
    reported as an assertion failure and not swallowed by the wait.
    """
    assert await wait_for(predicate, timeout), message


class NullEventHandler(FileSystemEventHandler):
    """A real handler that does nothing.

    ``observer.schedule`` is typed as taking a ``FileSystemEventHandler``, and
    this probe used to hand it a bare ``lambda *_a: None``. A callable is not a
    handler, and mypy was right: ``observer.schedule`` hands the object to the
    emitter, which calls ``dispatch`` on it. It happened to work on both
    platforms tested, so this was a latent break as well as a type error, and
    the fix is a subclass rather than a deletion because the point of the probe
    is to schedule something an observer will actually accept at runtime as
    well as at type-check time (issue #125 AC-3).
    """


def real_watch_supported() -> bool:
    """Whether this platform can run a real ``watchdog`` observer at all.

    Windows uses ``ReadDirectoryChangesW`` and Linux ``inotify``; a few CI
    sandboxes support neither, and those tests skip rather than fail.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        observer = Observer()
        try:
            observer.schedule(NullEventHandler(), tmp, recursive=True)
            observer.start()
        except (OSError, NotImplementedError, RuntimeError):
            return False
        observer.stop()
        observer.join(timeout=5.0)
    return True


REAL_WATCH_SUPPORTED = real_watch_supported()

__all__ = [
    "GUARDED_SIGNALS",
    "REPO_ROOT",
    "SIGNAL_GUARD",
    "WAIT_UNTIL_TIMEOUT_S",
    "FakeObserver",
    "NullEventHandler",
    "RecordingWatcher",
    "REAL_WATCH_SUPPORTED",
    "SignalSafetyGuard",
    "signal_name",
    "wait_for",
    "wait_until",
    "read_recorded_pids",
    "tree_command",
    "write_tree_script",
    "sys",
]


# --- the real process tree ------------------------------------------------

#: A three-process tree: this script (as ``parent``) spawns the same script as
#: ``child``, and every one of them appends its pid to a file before it does
#: anything else. "The tree is gone" is then a question about **pids** rather
#: than about the launcher's ``poll()``, which is the assertion that
#: distinguishes a real tree-stop from a launcher-only kill (issue #125 Q8d).
#:
#: ``sys.executable`` and ``__file__`` are both quoted at the call site
#: because a Windows install under ``C:\Program Files\Python312\`` contains a
#: space, and preserving the operator's quotes is exactly what the
#: single-string command line exists for.
TREE_SCRIPT = """\
import os
import subprocess
import sys
import time

role, pids_file = sys.argv[1], sys.argv[2]

@@REFUSE_POLITE@@

with open(pids_file, "a", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()}\\n")
    if role == "parent" and sys.platform == "win32":
        # cmd.exe is a real, separate process on Windows and its pid is worth
        # asserting on. On POSIX `/bin/sh -c` execs its only command, so the
        # python parent *is* the launcher and getppid() would name the test
        # process itself -- which this suite must never assert is dead.
        handle.write(f"{os.getppid()}\\n")
    handle.flush()

if role == "parent":
    subprocess.Popen([sys.executable, __file__, "child", pids_file])

time.sleep(300)
"""

#: The same tree with the polite request refused. Cooperative stops are
#: defeatable on both platforms -- ``SetConsoleCtrlHandler(..., TRUE)`` /
#: ``SIG_IGN`` for ``SIGBREAK`` on Windows, a ``SIGTERM`` handler on POSIX --
#: which is the whole reason a forced tier exists.
REFUSE_POLITE_POSIX = "signal.signal(signal.SIGTERM, signal.SIG_IGN)"
REFUSE_POLITE_WINDOWS = "signal.signal(signal.SIGBREAK, signal.SIG_IGN)"

REFUSE_POLITE_SNIPPET = """\
import signal

if sys.platform == "win32":
    {windows}
else:
    {posix}
"""


def read_recorded_pids(pids_file: Path) -> list[int]:
    """Every pid the tree has written so far, in the order it wrote them."""
    if not pids_file.exists():
        return []
    return [
        int(line)
        for line in pids_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_tree_script(
    directory: Path, *, refuse_polite: bool = False
) -> tuple[Path, Path]:
    """Write the tree script into ``directory``; return ``(script, pids_file)``.

    The snippet is substituted with ``str.replace`` and not ``str.format``:
    the script is full of f-strings, and ``format`` would try to resolve
    ``{os.getpid()}`` against *this* module's namespace.
    """
    refuse = ""
    if refuse_polite:
        refuse = REFUSE_POLITE_SNIPPET.format(
            windows=REFUSE_POLITE_WINDOWS, posix=REFUSE_POLITE_POSIX
        )
    script = directory / f"tree-{int(refuse_polite)}.py"
    script.write_text(
        TREE_SCRIPT.replace("@@REFUSE_POLITE@@", refuse), encoding="utf-8"
    )
    return script, directory / f"pids-{int(refuse_polite)}.txt"


def tree_command(script: Path, pids_file: Path) -> str:
    """The command line an operator would have typed for that tree.

    Quoted throughout, because a Windows install under
    ``C:\\Program Files\\Python312\\`` contains a space and preserving the
    operator's quotes is exactly what the single-string command line exists
    for. One program on both platforms, not a shell built-in: ``echo hello``
    is a ``cmd`` built-in *and* an ``sh`` built-in with different escape
    processing, and a test that only passes under one of them is not testing
    the thing the agent does (issue #125 Q8a).
    """
    return f'"{sys.executable}" "{script}" parent "{pids_file}"'


@pytest.fixture
def tree_script(tmp_path: Path) -> Callable[..., tuple[str, Path]]:
    """Build a runnable three-process tree; return ``(command, pids_file)``."""

    def build(*, refuse_polite: bool = False) -> tuple[str, Path]:
        script, pids = write_tree_script(tmp_path, refuse_polite=refuse_polite)
        return tree_command(script, pids), pids

    return build
